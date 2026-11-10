#ifndef C6_LUA_POLICY_API_H
#define C6_LUA_POLICY_API_H
#include <stdint.h>
/* Shared with c6-native-arena/native/external_policy.h. Append-only within v1.
 * All pointers are borrowed for this synchronous call; no engine pointers cross.
 * Native indices are zero based; Lua indices are one based.
 * Option flags: 1 = target coordinates known; 2 = origin coordinates known. */
struct c6_policy_option_v1 {
    const char *name;
    uint32_t actor;
    int32_t value, x, y, origin_x, origin_y;
    uint32_t flags;
};
struct c6_policy_request_v1 {
    uint32_t abi_version;
    const char *kind;
    int32_t owner;
    uint32_t count, random_index;
    const struct c6_policy_option_v1 *options;
};
/* Returns 0 on success. An error MUST stop execution, never use built-in AI. */
int c6_arena_choose_v1(const struct c6_policy_request_v1 *, uint32_t *selected);
int c6_arena_policy_reset_v1(void);
#endif
