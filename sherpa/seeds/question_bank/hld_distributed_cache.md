# HLD: Distributed Cache

## Problem

Design a distributed caching system similar to Redis or Memcached that can serve millions of requests per second with sub-millisecond latency.

## Scale hints

- 10M DAU, peak 100K RPS
- Cache hit ratio target: >95%
- Data size: ~1KB per entry average, up to 100M entries
- Latency: p99 < 5ms
- Availability: 99.99%

## Topics to cover

- Cache eviction policies (LRU, LFU, TTL)
- Consistent hashing for partitioning
- Replication strategy (leader-follower vs leaderless)
- Cache invalidation and coherence
- Hot key problem and solutions
- Write-through vs write-behind vs cache-aside patterns
