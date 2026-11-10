#ifndef C6_POLICY_CONTEXT_API_H
#define C6_POLICY_CONTEXT_API_H
#include <stdint.h>
/* Optional richer context interface. All values are copied into Lua during the
 * synchronous call. Object children have keys; array children are ordered.
 * No engine pointers or Lua state escape. This does not enumerate native actions. */
enum c6_value_type_v1 { C6_NULL, C6_BOOL, C6_NUMBER, C6_STRING, C6_ARRAY, C6_OBJECT };
struct c6_value_v1 {
    const char *key;
    uint32_t type;
    double number;
    const char *text;
    uint32_t count;
    const struct c6_value_v1 *children;
};
/* Context: {owner, kind, options:[...], random_index, observation?:{...}}.
 * Lua and context option indices are one based; selected output is zero based. */
int c6_arena_choose_context_v1(int32_t owner,const struct c6_value_v1 *,uint32_t *selected);
const char *c6_arena_last_reason_v1(void);
#endif
