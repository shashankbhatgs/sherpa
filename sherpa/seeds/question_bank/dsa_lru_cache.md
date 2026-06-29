# DSA: LRU Cache

## Problem

Implement an LRU (Least Recently Used) cache that supports `get` and `put` operations in O(1) time.

```
LRUCache(capacity: int)
get(key: int) -> int          # return -1 if not found
put(key: int, value: int)     # evict LRU entry if at capacity
```

## Constraints

- 1 <= capacity <= 3000
- 0 <= key <= 10^4
- 0 <= value <= 10^5
- Up to 2 * 10^5 calls to get and put

## Key insight

Combine a hash map (O(1) lookup) with a doubly linked list (O(1) move-to-front and eviction). The map stores key -> node; the list maintains recency order with sentinel head/tail nodes to avoid null checks.

## Follow-ups

- Thread-safe version with fine-grained locking
- LFU cache variant
- Distributed LRU across multiple nodes
- TTL support
