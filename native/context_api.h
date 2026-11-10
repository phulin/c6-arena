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
/* Optional demand-driven observation interface. Query results are borrowed
 * only during this synchronous call and contain copied values, never engine
 * pointers. INT32_MIN denotes an absent query argument. Status 1 means an
 * unknown query; hidden/missing actors return a successful C6_NULL value. */
struct c6_observation_source_v2 {
    uint32_t abi_version;
    uint32_t field_count;
    uint64_t revision;
    const char *const *fields;
    void *opaque;
    int (*query)(void *opaque,int32_t owner,const char *kind,int32_t a,int32_t b,
                 const struct c6_value_v1 **result);
};
/* Decision fields retain v1 meanings. The bridge supplies observation and
 * world query helpers, retaining fetched Lua values per owner and revision. */
int c6_arena_choose_context_v2(int32_t owner,const struct c6_value_v1 *,
                             const struct c6_observation_source_v2 *,uint32_t *selected);
const char *c6_arena_last_reason_v1(void);
#endif
