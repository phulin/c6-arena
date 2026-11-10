/* Minimal LuaJIT/Lua 5.1 C API declarations. No dependency on SDK headers.
 * ABI reference: https://www.lua.org/manual/5.1/manual.html#3 */
#ifndef C6_LUA51_H
#define C6_LUA51_H
#include <stddef.h>
#include <stdint.h>
typedef struct lua_State lua_State;
typedef struct lua_Debug lua_Debug;
typedef int (*lua_CFunction)(lua_State *);
typedef void (*lua_Hook)(lua_State *, lua_Debug *);
typedef void *(*lua_Alloc)(void *, void *, size_t, size_t);
enum { LUA_TNIL=0, LUA_TNUMBER=3, LUA_TTABLE=5, LUA_TFUNCTION=6,
       LUA_REGISTRYINDEX=-10000, LUA_GLOBALSINDEX=-10002, LUA_MASKCOUNT=8 };
extern lua_State *lua_newstate(lua_Alloc, void *);
extern void lua_close(lua_State *);
extern void lua_settop(lua_State *, int);
extern int lua_checkstack(lua_State *, int);
extern int lua_type(lua_State *, int);
extern void lua_pushnil(lua_State *);
extern void lua_pushinteger(lua_State *, ptrdiff_t);
extern void lua_pushnumber(lua_State *, double);
extern void lua_pushboolean(lua_State *, int);
extern void lua_pushstring(lua_State *, const char *);
extern void lua_pushcclosure(lua_State *, lua_CFunction, int);
extern void lua_createtable(lua_State *, int, int);
extern void lua_getfield(lua_State *, int, const char *);
extern void lua_setfield(lua_State *, int, const char *);
extern void lua_rawgeti(lua_State *, int, int);
extern void lua_rawseti(lua_State *, int, int);
extern double lua_tonumber(lua_State *, int);
extern const char *lua_tolstring(lua_State *, int, size_t *);
extern int lua_pcall(lua_State *, int, int, int);
extern void lua_call(lua_State *, int, int);
extern int lua_sethook(lua_State *, lua_Hook, int, int);
extern int luaL_loadbufferx(lua_State *, const char *, size_t, const char *, const char *);
extern int luaL_ref(lua_State *, int);
extern int luaL_error(lua_State *, const char *, ...);
extern int luaopen_base(lua_State *);
extern int luaopen_table(lua_State *);
extern int luaopen_string(lua_State *);
extern int luaopen_math(lua_State *);
extern int luaJIT_setmode(lua_State *, int, int);
#endif
