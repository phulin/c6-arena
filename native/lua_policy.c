#define _POSIX_C_SOURCE 200809L
#include "policy_api.h"
#include "lua51.h"
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

/* Synchronous policy calls only, on the harness thread. A VM per owner keeps
 * strategy memory private. This limits accidental runaway scripts; it is not
 * a security boundary for hostile native code or a replacement for a process. */
#define MEMORY_LIMIT (16u * 1024u * 1024u)
#define SOURCE_LIMIT (1024u * 1024u)
#define HOOK_INTERVAL 1000
#define HOOK_TICKS 200
struct policy { lua_State *vm; size_t bytes; int ref, ticks; };
static struct policy policies[64];
static struct policy *active;
static const struct c6_policy_request_v1 *request;
static unsigned long sequence;
static FILE *trace;

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
static void log_choice(uint32_t selected,const char *error) {
    if(!trace)return;
    fprintf(trace,"{\"sequence\":%lu,\"owner\":%d,\"kind\":",sequence++,request->owner);
    json_string(trace,request->kind);
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
    fputs("}\n",trace);fflush(trace);
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
static int call_policy(lua_State *vm) {
    lua_rawgeti(vm,LUA_REGISTRYINDEX,active->ref);
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
    lua_setfield(vm,-2,"options");lua_call(vm,1,1);return 1;
}
int c6_arena_policy_reset_v1(void) {
    for(unsigned i=0;i<64;++i){if(policies[i].vm)lua_close(policies[i].vm);policies[i]=(struct policy){0};}
    active=NULL;request=NULL;sequence=0;
    if(trace){fclose(trace);trace=NULL;}
    return 0;
}
__attribute__((destructor)) static void release_policy_states(void) {
    c6_arena_policy_reset_v1();
}
int c6_arena_choose_v1(const struct c6_policy_request_v1 *r,uint32_t *selected) {
    if(!r||!selected||r->abi_version!=1||r->owner<0||r->owner>=64||
       !r->kind||!r->count||r->count>4096||!r->options||r->random_index>=r->count)return 197;
    request=r;active=policies+r->owner;
    const char *trace_path=getenv("C6_ARENA_POLICY_TRACE");
    if(!trace&&trace_path){trace=fopen(trace_path,"a");if(!trace)return fail(NULL,"cannot open trace");}
    if(!active->vm&&load_policy(active,r->owner))return 197;
    lua_State *vm=active->vm;lua_settop(vm,0);active->ticks=HOOK_TICKS;
    lua_pushcclosure(vm,call_policy,0);
    if(lua_pcall(vm,0,1,0))return fail(vm,"choice failed");
    double index=lua_tonumber(vm,-1);
    if(lua_type(vm,-1)!=LUA_TNUMBER||!(index>=1&&index<=r->count)||index!=(uint32_t)index)
        return fail(NULL,"choice must be an integer index into options");
    *selected=(uint32_t)index-1;lua_settop(vm,0);log_choice(*selected,NULL);
    return 0;
}
