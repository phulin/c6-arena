#define _POSIX_C_SOURCE 200809L
#include "policy_api.h"
#include "context_api.h"
#include "lua51.h"
#include <math.h>
#include <inttypes.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>

/* Synchronous policy calls only, on the harness thread. A VM per owner keeps
 * strategy memory private. This limits accidental runaway scripts; it is not
 * a security boundary for hostile native code or a replacement for a process. */
#define MEMORY_LIMIT (64u * 1024u * 1024u)
#define SOURCE_LIMIT (1024u * 1024u)
#define HOOK_INTERVAL 1000
#define HOOK_TICKS 1000
struct policy_timing {
    uint64_t calls, loads, failures, load_ns, marshal_ns, lua_ns, trace_ns, bridge_ns, query_ns, queries;
};
struct policy {
    lua_State *vm; size_t bytes; int ref, ticks;
    int observation_ref, world_ref;
    uint64_t revision;
    struct policy_timing timing;
};
static struct policy policies[64];
static struct policy *active;
static const struct c6_policy_request_v1 *request;
static unsigned long sequence;
static FILE *trace;
static FILE *timing_trace;
static const struct c6_value_v1 *context;
static const struct c6_observation_source_v2 *observation_source;
static char last_reason[512];
static unsigned context_nodes;
static int timing_enabled;
static uint64_t cpu_ns(void) {
    if(!timing_enabled)return 0;
    struct timespec t;clock_gettime(CLOCK_THREAD_CPUTIME_ID,&t);
    return (uint64_t)t.tv_sec*UINT64_C(1000000000)+(uint64_t)t.tv_nsec;
}
const char *c6_arena_policy_timing_json_v1(int32_t owner) {
    static char json[768];
    if(owner<0||owner>=64)return NULL;
    const struct policy_timing *t=&policies[owner].timing;
    snprintf(json,sizeof json,"{\"owner\":%d,\"calls\":%" PRIu64 ",\"loads\":%" PRIu64
        ",\"failures\":%" PRIu64 ",\"load_cpu_ns\":%" PRIu64 ",\"marshal_cpu_ns\":%" PRIu64
        ",\"lua_cpu_ns\":%" PRIu64 ",\"trace_cpu_ns\":%" PRIu64 ",\"bridge_cpu_ns\":%" PRIu64
        ",\"query_cpu_ns\":%" PRIu64 ",\"queries\":%" PRIu64 "}",
        owner,t->calls,t->loads,t->failures,t->load_ns,t->marshal_ns,t->lua_ns,t->trace_ns,t->bridge_ns,t->query_ns,t->queries);
    return json;
}

static void *allocate(void *ud, void *ptr, size_t old, size_t size) {
    struct policy *p=ud;
    if(!ptr)old=0;
    if(!size){free(ptr);p->bytes-=old;return NULL;}
    if(size>MEMORY_LIMIT || p->bytes-old>MEMORY_LIMIT-size)return NULL;
    void *next=realloc(ptr,size);
    if(next)p->bytes=p->bytes-old+size;
    return next;
}
static void budget(lua_State *vm, lua_Debug *debug) {
    (void)debug;
    if(--active->ticks<=0)luaL_error(vm,"instruction budget exceeded");
}
static void string_field(lua_State *vm,const char *key,const char *value) {
    lua_pushstring(vm,value?value:"");lua_setfield(vm,-2,key);
}
static void integer_field(lua_State *vm,const char *key,ptrdiff_t value) {
    lua_pushinteger(vm,value);lua_setfield(vm,-2,key);
}
static int lazy_next(lua_State *vm);
static int lazy_pairs(lua_State *vm);
static int initialize_libraries(lua_State *vm) {
    const struct {lua_CFunction open;const char *name;} libs[]={
        {luaopen_base,""},{luaopen_table,"table"},
        {luaopen_string,"string"},{luaopen_math,"math"}};
    for(unsigned i=0;i<sizeof libs/sizeof *libs;++i){
        lua_pushcclosure(vm,libs[i].open,0);lua_pushstring(vm,libs[i].name);
        lua_call(vm,1,0);
    }
    const char *remove[]={"dofile","loadfile","load","loadstring","getfenv",
        "setfenv","collectgarbage","newproxy","print","module","require"};
    for(unsigned i=0;i<sizeof remove/sizeof *remove;++i){
        lua_pushnil(vm);lua_setfield(vm,LUA_GLOBALSINDEX,remove[i]);
    }
    /* All random choices come from the native policy seed via random_index. */
    lua_getfield(vm,LUA_GLOBALSINDEX,"math");
    lua_pushnil(vm);lua_setfield(vm,-2,"random");
    lua_pushnil(vm);lua_setfield(vm,-2,"randomseed");
    lua_pushcclosure(vm,lazy_pairs,0);lua_setfield(vm,LUA_GLOBALSINDEX,"pairs");
    lua_pushcclosure(vm,lazy_next,0);lua_setfield(vm,LUA_GLOBALSINDEX,"next");
    lua_settop(vm,0);return 0;
}
static void json_string(FILE *out,const char *s) {
    fputc('"',out);
    for(const unsigned char *p=(const unsigned char *)(s?s:"");*p;++p){
        if(*p=='"'||*p=='\\')fprintf(out,"\\%c",*p);
        else if(*p<32||*p>=127)fprintf(out,"\\u%04x",*p);
        else fputc(*p,out);
    }
    fputc('"',out);
}
static void json_value(FILE *out,const struct c6_value_v1 *v) {
    switch(v->type){
    case C6_NULL:fputs("null",out);break;
    case C6_BOOL:fputs(v->number?"true":"false",out);break;
    case C6_NUMBER:fprintf(out,"%.17g",v->number);break;
    case C6_STRING:json_string(out,v->text);break;
    default:
        fputc(v->type==C6_ARRAY?'[':'{',out);
        for(unsigned i=0;i<v->count;++i){
            if(i)fputc(',',out);
            if(v->type==C6_OBJECT){json_string(out,v->children[i].key);fputc(':',out);}
            json_value(out,v->children+i);
        }
        fputc(v->type==C6_ARRAY?']':'}',out);
    }
}
static void log_choice_impl(uint32_t selected,const char *error) {
    if(!trace)return;
    fprintf(trace,"{\"sequence\":%lu,\"owner\":%d,\"kind\":",sequence++,request->owner);
    json_string(trace,request->kind);
    if(context){
        /* Use the same trace shape for both native interfaces. Every policy
         * input, including visible observations, remains replayable. */
        for(unsigned i=0;i<context->count;++i){
            const struct c6_value_v1 *field=context->children+i;
            if(!strcmp(field->key,"owner")||!strcmp(field->key,"kind"))continue;
            fputc(',',trace);json_string(trace,field->key);fputc(':',trace);json_value(trace,field);
        }
        if(observation_source)fprintf(trace,",\"observation_id\":%" PRIu64,observation_source->revision);
        fputs(",\"selected\":",trace);
        if(error)fputs("null",trace);else fprintf(trace,"%u",selected+1);
        fputs(",\"error\":",trace);
        if(error)json_string(trace,error);else fputs("null",trace);
        fputs(",\"reason\":",trace);json_string(trace,last_reason);
        fputs("}\n",trace);fflush(trace);return;
    }
    fprintf(trace,",\"random_index\":%u,\"options\":[",request->random_index+1);
    for(uint32_t i=0;i<request->count;++i){
        const struct c6_policy_option_v1 *o=request->options+i;
        if(i)fputc(',',trace);
        fprintf(trace,"{\"index\":%u,\"name\":",i+1);json_string(trace,o->name);
        fprintf(trace,",\"actor\":%u,\"value\":%d",o->actor,o->value);
        if(o->flags&1)fprintf(trace,",\"x\":%d,\"y\":%d",o->x,o->y);
        if(o->flags&2)fprintf(trace,",\"origin_x\":%d,\"origin_y\":%d",o->origin_x,o->origin_y);
        fputc('}',trace);
    }
    fputs("],\"selected\":",trace);
    if(error)fputs("null,\"error\":",trace);else fprintf(trace,"%u,\"error\":",selected+1);
    if(error)json_string(trace,error);else fputs("null",trace);
    fputs(",\"reason\":",trace);json_string(trace,last_reason);
    fputs("}\n",trace);fflush(trace);
}
static void log_choice(uint32_t selected,const char *error) {
    uint64_t start=cpu_ns();log_choice_impl(selected,error);
    if(active)active->timing.trace_ns+=cpu_ns()-start;
}
static int fail(lua_State *vm,const char *message) {
    const char *detail=vm?lua_tolstring(vm,-1,NULL):NULL;
    fprintf(stderr,"C6_LUA_POLICY owner %d %s: %s\n",request->owner,message,detail?detail:"");
    log_choice(0,detail?detail:message);
    if(vm)lua_settop(vm,0);
    return 197;
}
static int load_policy(struct policy *p,int owner) {
    char key[64];snprintf(key,sizeof key,"C6_ARENA_LUA_POLICY_%d",owner);
    const char *path=getenv(key);if(!path)path=getenv("C6_ARENA_LUA_POLICY");
    if(!path)return fail(NULL,"no Lua policy configured for owner");
    FILE *input=fopen(path,"rb");if(!input)return fail(NULL,"cannot open Lua policy");
    char *source=malloc(SOURCE_LIMIT+1);if(!source){fclose(input);return fail(NULL,"source allocation failed");}
    size_t length=fread(source,1,SOURCE_LIMIT+1,input);
    int bad=ferror(input)||length>SOURCE_LIMIT;fclose(input);
    if(bad){free(source);return fail(NULL,"policy exceeds source limit or read failed");}
    p->vm=lua_newstate(allocate,p);if(!p->vm){free(source);return fail(NULL,"VM allocation failed");}
    /* JIT off: instruction hooks must also cover hot loops. */
    luaJIT_setmode(p->vm,0,0);
    lua_sethook(p->vm,budget,LUA_MASKCOUNT,HOOK_INTERVAL);
    p->ticks=HOOK_TICKS;
    lua_pushcclosure(p->vm,initialize_libraries,0);
    if(lua_pcall(p->vm,0,0,0)){free(source);return fail(p->vm,"library initialization failed");}
    int status=luaL_loadbufferx(p->vm,source,length,path,"t");free(source);
    if(status||lua_pcall(p->vm,0,1,0))return fail(p->vm,"loading policy failed");
    if(lua_type(p->vm,-1)!=LUA_TFUNCTION)return fail(NULL,"policy must return a function");
    p->ref=luaL_ref(p->vm,LUA_REGISTRYINDEX);
    return 0;
}
static void push_value(lua_State *vm,const struct c6_value_v1 *v,unsigned depth) {
    if(depth>32||++context_nodes>250000)luaL_error(vm,"context exceeds size/depth limit");
    if(!lua_checkstack(vm,4))luaL_error(vm,"context exceeds Lua stack limit");
    switch(v->type){
    case C6_NULL:lua_pushnil(vm);return;
    case C6_BOOL:lua_pushboolean(vm,v->number!=0);return;
    case C6_NUMBER:
        if(!isfinite(v->number))luaL_error(vm,"context number must be finite");
        lua_pushnumber(vm,v->number);return;
    case C6_STRING:
        if(!v->text)luaL_error(vm,"context string missing");
        lua_pushstring(vm,v->text);return;
    case C6_ARRAY:case C6_OBJECT:break;
    default:luaL_error(vm,"unknown context type");return;
    }
    if(v->count>250000||(v->count&&!v->children))luaL_error(vm,"invalid context children");
    lua_createtable(vm,v->type==C6_ARRAY?(int)v->count:0,v->type==C6_OBJECT?(int)v->count:0);
    for(unsigned i=0;i<v->count;++i){
        if(v->type==C6_OBJECT&&!v->children[i].key)luaL_error(vm,"context object key missing");
        push_value(vm,v->children+i,depth+1);
        if(v->type==C6_ARRAY)lua_rawseti(vm,-2,(int)i+1);
        else lua_setfield(vm,-2,v->children[i].key);
    }
}
#define UPVALUE(I) (LUA_GLOBALSINDEX-(I))
static void trace_world(int owner,uint64_t revision) {
    if(!trace)return;
    uint64_t start=cpu_ns();
    fprintf(trace,"{\"record\":\"world\",\"owner\":%d,\"observation_id\":%" PRIu64 ",\"fields\":[",owner,revision);
    for(unsigned i=0;i<observation_source->field_count;++i){
        if(i)fputc(',',trace);
        json_string(trace,observation_source->fields[i]);
    }
    fputs("]}\n",trace);fflush(trace);active->timing.trace_ns+=cpu_ns()-start;
}
static void query_result(lua_State *vm,int owner,uint64_t revision,const char *kind,int32_t a,int32_t b) {
    char key[256];int length=snprintf(key,sizeof key,"%s:%d:%d",kind,a,b);
    if(length<0||(size_t)length>=sizeof key)luaL_error(vm,"query name too long");
    lua_pushstring(vm,key);lua_rawget(vm,UPVALUE(3));
    if(lua_type(vm,-1)==LUA_TTABLE){
        lua_getfield(vm,-1,"value");lua_remove(vm,-2);return;
    }
    lua_settop(vm,-2);
    if(!observation_source||!request||request->owner!=owner||observation_source->revision!=revision)
        luaL_error(vm,"observation revision expired; retain fetched values instead");
    const struct c6_value_v1 *value=NULL;
    uint64_t start=cpu_ns();
    int status=observation_source->query(observation_source->opaque,owner,kind,a,b,&value);
    active->timing.query_ns+=cpu_ns()-start;++active->timing.queries;
    const struct c6_value_v1 missing={.type=C6_NULL};
    if(status==1)value=&missing;
    else if(status||!value)luaL_error(vm,"native observation query failed: %s (%d)",kind,status);
    start=cpu_ns();push_value(vm,value,0);active->timing.marshal_ns+=cpu_ns()-start;
    int result=lua_gettop(vm);
    if(trace){
        start=cpu_ns();
        fprintf(trace,"{\"record\":\"observation\",\"owner\":%d,\"observation_id\":%" PRIu64 ",\"query\":",owner,revision);
        json_string(trace,kind);fprintf(trace,",\"a\":%d,\"b\":%d,\"value\":",a,b);json_value(trace,value);
        fputs("}\n",trace);fflush(trace);active->timing.trace_ns+=cpu_ns()-start;
    }
    lua_pushstring(vm,key);lua_createtable(vm,0,1);
    lua_pushvalue(vm,result);lua_setfield(vm,-2,"value");lua_rawset(vm,UPVALUE(3));
}
static int observation_index(lua_State *vm) {
    if(lua_type(vm,2)!=LUA_TSTRING){lua_pushnil(vm);return 1;}
    lua_pushvalue(vm,2);lua_rawget(vm,UPVALUE(4));
    int known=lua_type(vm,-1)!=LUA_TNIL;lua_settop(vm,-2);
    if(!known){lua_pushnil(vm);return 1;}
    const char *kind=lua_tolstring(vm,2,NULL);
    query_result(vm,(int)lua_tonumber(vm,UPVALUE(1)),(uint64_t)lua_tonumber(vm,UPVALUE(2)),kind,INT32_MIN,INT32_MIN);
    lua_pushvalue(vm,2);lua_pushvalue(vm,-2);lua_rawset(vm,1);return 1;
}
static int load_all_observations(lua_State *vm) {
    size_t n=lua_objlen(vm,UPVALUE(1));
    for(size_t i=1;i<=n;++i){
        lua_rawgeti(vm,UPVALUE(1),(int)i);
        const char *key=lua_tolstring(vm,-1,NULL);
        lua_getfield(vm,1,key);lua_settop(vm,-3);
    }
    return 0;
}
static void materialize_if_lazy(lua_State *vm) {
    int top=lua_gettop(vm);
    if(lua_getmetatable(vm,1)){
        lua_getfield(vm,-1,"__c6_load_all");
        if(lua_type(vm,-1)==LUA_TFUNCTION){lua_pushvalue(vm,1);lua_call(vm,1,0);}
    }
    lua_settop(vm,top);
}
static int lazy_next(lua_State *vm) {
    materialize_if_lazy(vm);lua_settop(vm,2);
    if(lua_next(vm,1))return 2;
    lua_pushnil(vm);return 1;
}
static int lazy_pairs(lua_State *vm) {
    materialize_if_lazy(vm);
    lua_pushcclosure(vm,lazy_next,0);lua_pushvalue(vm,1);lua_pushnil(vm);return 3;
}
static int32_t query_integer(lua_State *vm,int index) {
    double value=lua_tonumber(vm,index);
    if(lua_type(vm,index)!=LUA_TNUMBER||!isfinite(value)||value<INT32_MIN||value>INT32_MAX||value!=(int32_t)value)
        luaL_error(vm,"world query argument must be a 32-bit integer");
    return (int32_t)value;
}
static int world_query(lua_State *vm) {
    int owner=(int)lua_tonumber(vm,UPVALUE(1));
    uint64_t revision=(uint64_t)lua_tonumber(vm,UPVALUE(2));
    const char *kind=lua_tolstring(vm,UPVALUE(4),NULL);
    int first=lua_type(vm,1)==LUA_TTABLE?2:1;
    int32_t a=INT32_MIN,b=INT32_MIN;
    if(!strcmp(kind,"plot")||!strcmp(kind,"plot_yields")){
        a=query_integer(vm,first);b=query_integer(vm,first+1);
    }else if(!strcmp(kind,"city")||!strcmp(kind,"unit")){
        a=query_integer(vm,first);
        b=lua_gettop(vm)>=first+1?query_integer(vm,first+1):owner;
    }
    query_result(vm,owner,revision,kind,a,b);return 1;
}
static void query_upvalues(lua_State *vm,int owner,uint64_t revision,int cache) {
    lua_pushinteger(vm,owner);lua_pushnumber(vm,(double)revision);lua_pushvalue(vm,cache);
}
static void attach_observations(lua_State *vm) {
    int top=lua_gettop(vm),owner=request->owner;
    uint64_t revision=observation_source->revision;
    if(!active->observation_ref||active->revision!=revision){
        if(active->observation_ref)luaL_unref(vm,LUA_REGISTRYINDEX,active->observation_ref);
        if(active->world_ref)luaL_unref(vm,LUA_REGISTRYINDEX,active->world_ref);
        lua_createtable(vm,(int)observation_source->field_count,(int)observation_source->field_count);
        int fields=lua_gettop(vm);
        for(unsigned i=0;i<observation_source->field_count;++i){
            const char *name=observation_source->fields[i];
            lua_pushstring(vm,name);lua_rawseti(vm,fields,(int)i+1);
            lua_pushboolean(vm,1);lua_setfield(vm,fields,name);
        }
        lua_createtable(vm,0,16);int cache=lua_gettop(vm);
        lua_createtable(vm,0,(int)observation_source->field_count);int observation=lua_gettop(vm);
        lua_createtable(vm,0,3);
        query_upvalues(vm,owner,revision,cache);lua_pushvalue(vm,fields);
        lua_pushcclosure(vm,observation_index,4);lua_setfield(vm,-2,"__index");
        lua_pushvalue(vm,fields);lua_pushcclosure(vm,load_all_observations,1);lua_setfield(vm,-2,"__c6_load_all");
        lua_pushstring(vm,"observation");lua_setfield(vm,-2,"__metatable");
        lua_setmetatable(vm,observation);
        lua_pushvalue(vm,observation);active->observation_ref=luaL_ref(vm,LUA_REGISTRYINDEX);
        lua_createtable(vm,0,(int)observation_source->field_count+6);int world=lua_gettop(vm);
        for(unsigned i=0;i<observation_source->field_count;++i){
            const char *name=observation_source->fields[i];
            query_upvalues(vm,owner,revision,cache);lua_pushstring(vm,name);
            lua_pushcclosure(vm,world_query,4);lua_setfield(vm,world,name);
        }
        static const char *const methods[]={"plot","plot_yields","plots_basic","city","unit"};
        for(unsigned i=0;i<sizeof methods/sizeof *methods;++i){
            query_upvalues(vm,owner,revision,cache);lua_pushstring(vm,methods[i]);
            lua_pushcclosure(vm,world_query,4);lua_setfield(vm,world,methods[i]);
        }
        lua_pushnumber(vm,(double)revision);lua_setfield(vm,world,"revision");
        lua_pushvalue(vm,world);active->world_ref=luaL_ref(vm,LUA_REGISTRYINDEX);
        active->revision=revision;lua_settop(vm,top);trace_world(owner,revision);
    }
    lua_rawgeti(vm,LUA_REGISTRYINDEX,active->observation_ref);lua_setfield(vm,top,"observation");
    lua_rawgeti(vm,LUA_REGISTRYINDEX,active->world_ref);lua_setfield(vm,top,"world");
    lua_pushnumber(vm,(double)revision);lua_setfield(vm,top,"observation_id");
}
static int call_policy(lua_State *vm) {
    uint64_t start=cpu_ns();
    lua_rawgeti(vm,LUA_REGISTRYINDEX,active->ref);
    if(context){
        uint64_t trace_before=active->timing.trace_ns;
        push_value(vm,context,0);
        if(observation_source)attach_observations(vm);
        active->timing.marshal_ns+=cpu_ns()-start-(active->timing.trace_ns-trace_before);
        lua_call(vm,1,2);return 2;
    }
    lua_createtable(vm,0,4);
    string_field(vm,"kind",request->kind);integer_field(vm,"owner",request->owner);
    integer_field(vm,"random_index",request->random_index+1);
    lua_createtable(vm,(int)request->count,0);
    for(uint32_t i=0;i<request->count;++i){
        const struct c6_policy_option_v1 *o=request->options+i;
        lua_createtable(vm,0,9);
        integer_field(vm,"index",i+1);string_field(vm,"name",o->name);
        integer_field(vm,"actor",o->actor);integer_field(vm,"value",o->value);
        if(o->flags&1){integer_field(vm,"x",o->x);integer_field(vm,"y",o->y);}
        if(o->flags&2){integer_field(vm,"origin_x",o->origin_x);integer_field(vm,"origin_y",o->origin_y);}
        lua_rawseti(vm,-2,(int)i+1);
    }
    lua_setfield(vm,-2,"options");active->timing.marshal_ns+=cpu_ns()-start;
    lua_call(vm,1,2);return 2;
}
int c6_arena_policy_reset_v1(void) {
    for(unsigned i=0;i<64;++i){
        if(timing_enabled&&policies[i].timing.calls)
            fprintf(stderr,"C6_LUA_TIMING %s\n",c6_arena_policy_timing_json_v1((int32_t)i));
        if(policies[i].vm)lua_close(policies[i].vm);
        policies[i]=(struct policy){0};
    }
    active=NULL;request=NULL;sequence=0;context=NULL;observation_source=NULL;last_reason[0]=0;
    if(trace){fclose(trace);trace=NULL;}
    if(timing_trace){fclose(timing_trace);timing_trace=NULL;}
    return 0;
}
__attribute__((destructor)) static void release_policy_states(void) {
    c6_arena_policy_reset_v1();
}
static int execute_choice_impl(const struct c6_policy_request_v1 *r,uint32_t *selected) {
    if(!r||!selected||r->abi_version!=1||r->owner<0||r->owner>=64||
       !r->kind||!r->count||r->count>4096||(!context&&!r->options)||r->random_index>=r->count)return 197;
    last_reason[0]=0;context_nodes=0;
    request=r;active=policies+r->owner;
    const char *trace_path=getenv("C6_ARENA_POLICY_TRACE");
    if(!trace&&trace_path){trace=fopen(trace_path,"a");if(!trace)return fail(NULL,"cannot open trace");}
    if(!active->vm){
        uint64_t start=cpu_ns();++active->timing.loads;
        int status=load_policy(active,r->owner);active->timing.load_ns+=cpu_ns()-start;
        if(status)return status;
    }
    lua_State *vm=active->vm;lua_settop(vm,0);active->ticks=HOOK_TICKS;
    lua_pushcclosure(vm,call_policy,0);
    uint64_t start=cpu_ns(),marshal_before=active->timing.marshal_ns,
        query_before=active->timing.query_ns,trace_before=active->timing.trace_ns;
    int status=lua_pcall(vm,0,2,0);
    uint64_t elapsed=cpu_ns()-start,other=(active->timing.marshal_ns-marshal_before)
        +(active->timing.query_ns-query_before)+(active->timing.trace_ns-trace_before);
    active->timing.lua_ns+=elapsed>=other?elapsed-other:0;
    if(status)return fail(vm,"choice failed");
    double index=lua_tonumber(vm,-2);
    if(lua_type(vm,-2)!=LUA_TNUMBER||!(index>=1&&index<=r->count)||index!=(uint32_t)index)
        return fail(NULL,"choice must be an integer index into options");
    const char *reason=lua_tolstring(vm,-1,NULL);
    if(reason)snprintf(last_reason,sizeof last_reason,"%s",reason);
    *selected=(uint32_t)index-1;lua_settop(vm,0);log_choice(*selected,NULL);
    return 0;
}
static int execute_choice(const struct c6_policy_request_v1 *r,uint32_t *selected) {
    const char *enabled=getenv("C6_ARENA_POLICY_TIMING");timing_enabled=enabled&&!strcmp(enabled,"1");
    uint64_t start=cpu_ns();int status=execute_choice_impl(r,selected);
    if(r&&r->owner>=0&&r->owner<64){
        struct policy_timing *t=&policies[r->owner].timing;
        ++t->calls;t->failures+=!!status;t->bridge_ns+=cpu_ns()-start;
        const char *path=getenv("C6_ARENA_POLICY_TIMING_FILE");
        if(timing_enabled&&path){
            if(!timing_trace)timing_trace=fopen(path,"a");
            if(timing_trace){
                fprintf(timing_trace,"%s\n",c6_arena_policy_timing_json_v1(r->owner));
                fflush(timing_trace);
            }
        }
    }
    return status;
}
int c6_arena_choose_v1(const struct c6_policy_request_v1 *r,uint32_t *selected) {
    context=NULL;observation_source=NULL;return execute_choice(r,selected);
}
const char *c6_arena_last_reason_v1(void) {return last_reason;}
static int choose_context(int32_t owner,const struct c6_value_v1 *v,
                          const struct c6_observation_source_v2 *source,uint32_t *selected) {
    if(!v||v->type!=C6_OBJECT||v->count>64||!v->children)return 197;
    const struct c6_value_v1 *kind=NULL,*options=NULL,*random=NULL,*seat=NULL;
    for(unsigned i=0;i<v->count;++i){
        const struct c6_value_v1 *field=v->children+i;
        if(!field->key)return 197;
        if(!strcmp(field->key,"kind"))kind=field;
        if(!strcmp(field->key,"options"))options=field;
        if(!strcmp(field->key,"random_index"))random=field;
        if(!strcmp(field->key,"owner"))seat=field;
    }
    if(!kind||kind->type!=C6_STRING||!kind->text||!options||options->type!=C6_ARRAY||
       !random||random->type!=C6_NUMBER||!isfinite(random->number)||random->number<1||
       random->number>options->count||random->number!=(uint32_t)random->number||
       !seat||seat->type!=C6_NUMBER||seat->number!=owner)return 197;
    struct c6_policy_request_v1 r={.abi_version=1,.kind=kind->text,.owner=owner,
        .count=options->count,.random_index=(uint32_t)random->number-1};
    context=v;observation_source=source;
    int status=execute_choice(&r,selected);context=NULL;observation_source=NULL;return status;
}
int c6_arena_choose_context_v1(int32_t owner,const struct c6_value_v1 *v,uint32_t *selected) {
    return choose_context(owner,v,NULL,selected);
}
int c6_arena_choose_context_v2(int32_t owner,const struct c6_value_v1 *v,
                             const struct c6_observation_source_v2 *source,uint32_t *selected) {
    if(!source||source->abi_version!=2||!source->query||source->field_count>64||
       (source->field_count&&!source->fields)||source->revision>UINT64_C(9007199254740991))return 197;
    for(unsigned i=0;i<source->field_count;++i)if(!source->fields[i])return 197;
    return choose_context(owner,v,source,selected);
}
