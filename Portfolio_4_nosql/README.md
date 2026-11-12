# Portfolio 4 — NoSQL and Advanced Data Models

**Implemented:** Redis 7 key-value store (with its hash, sorted-set, stream, geo and set types),
loaded from the same AquaSense+ PostgreSQL database.
**Designed for comparison:** a MongoDB document model of the same data.

## Files

| File | Purpose |
|---|---|
| `redis_aquasense.py` | Loads PostgreSQL data into Redis and demonstrates 11 access patterns (S1–S11) |
| `config/redis-aquasense.conf` | Durability (AOF, `everysec`) and memory policy (`volatile-lru`) |
| `config/pgbench_latest_reading.sql` | Query used for the fair server benchmark |
| `evidence/redis_demo_output.txt` | Full output of every Redis command |
| `evidence/server_benchmark_output.txt` | `redis-benchmark` vs `pgbench` |
| `evidence/redis_key_patterns.txt` | Key namespace actually present in Redis |
| `document-model/example_batch_document.json` | A real batch document generated from PostgreSQL |
| `document-model/mongodb_design.js` | MongoDB collections, validation, indexes, aggregation (design — see note) |

**Run:** `redis-server config/redis-aquasense.conf` then `python3 redis_aquasense.py`
(needs `redis`, `psycopg2-binary`).

## 1. Why AquaSense+ needs a NoSQL store

The relational database (Portfolios 1–3) is the **system of record**: money, stock, harvests and
anything needing joins, constraints and transactions. But part of the workload has a different shape:

| Workload | Shape | Problem for the relational DB alone |
|---|---|---|
| Sensor readings from IoT probes | Many small writes, append-only | Write load and indexes on the primary DB (Portfolio 2: indexes added ~55% to insert time) |
| "Latest reading" on every app open | Same tiny read, very often | Repeated planning/executing of an identical query |
| Low-oxygen alerts | Must react within seconds | Needs a queue/notification mechanism, not polling |
| OTP codes, sync de-duplication, dashboard cache | Short-lived data | Needs automatic expiry (TTL) — no cleanup jobs |
| Cooperative rankings, "farms near me" | Ranking and geo lookup | Possible in SQL (PostGIS), but Redis has these structures built in |

## 2. Key-value data model (key design)

| Key pattern | Redis type | Holds | Access pattern |
|---|---|---|---|
| `pond:{unit_id}:latest` | Hash | Latest pH, DO, temperature, ammonia, timestamp | `HGETALL` on dashboard open (O(1)) |
| `pond:{unit_id}:do` | Sorted set (score = epoch seconds) | DO readings for the app chart | `ZRANGEBYSCORE` time window; `ZREMRANGEBYSCORE` retention |
| `farm:{farm_id}:ponds` | Set | Unit IDs on a farm | `SMEMBERS` then pipelined `HGETALL`s |
| `farm:{farm_id}` | Hash | Name, state, LGA | Label lookups |
| `stream:readings` | Stream (`MAXLEN ~100000`) | Incoming sensor messages | `XADD`; consumer groups `alert-workers`, `db-writers` |
| `farm:{farm_id}:alerts` | Sorted set (score = time) | Low-oxygen alerts | `ZRANGE` newest alerts; plus `PUBLISH` to push notification |
| `sync:{device}:{record}` | String, `EX 86400` | Upload already processed | `SET NX` idempotency check |
| `otp:{phone}` | String, `EX 300` | Login code | Expires automatically |
| `coop:{coop_id}:fcr` | Sorted set (score = FCR) | Farm ranking | `ZRANGE … WITHSCORES`, `ZRANK` |
| `farms:geo` | Geo (sorted set) | Farm coordinates | `GEOSEARCH … BYRADIUS` |
| `cache:dash:{state}:{year}` | String (JSON), `EX 600` | Cooperative dashboard | Cache-aside |

Keys are designed **from the queries backwards** (query-first modelling) and namespaced with
`entity:id:attribute` so they can be scanned, expired and — in Portfolio 5 — sharded by hash slot.
Note that `farm_id` is part of every farm-scoped key, which matters for Redis Cluster.

## 3. What the implementation showed (`evidence/redis_demo_output.txt`)

- **S1 Load:** 905 latest-reading hashes, 23,597 time-window entries and 180 geo points loaded in
  ~0.4 s using pipelining.
- **S3 Time window:** the night-time oxygen dip for pond 316 is visible (2.09 mg/L at 03:00, rising to
  7.63 by 15:00). Retention trimming removed 48 readings older than 72 h.
- **S4 Stream pipeline:** 12 simulated readings from pond 3 → `alert-workers` raised **4 low-oxygen
  alerts** (1.7–2.3 mg/L) and updated the latest-reading hash in a `MULTI/EXEC` transaction;
  `XPENDING` = 0 proves every message was acknowledged. A second consumer group (`db-writers`)
  batch-inserted the same 12 readings into PostgreSQL — verified with SQL. Two independent consumers
  read the same stream at their own pace.
- **S5 Idempotency:** the first `SET NX` returns `True`, the retry `None` → a phone that retries an
  upload after a dropped connection cannot create a duplicate feeding record.
- **S6 TTL:** an OTP set with `EX 2` returns `None` after 2.2 s — no cleanup job required.
- **S7 Leaderboard:** best FCR in the Ibadan cooperative is 1.19 (Okafor Fish Farm).
- **S8 Geo:** 23 farms within 60 km of Epe, sorted by distance and flagged harvest-ready.
- **S9 Lua:** `use_feed` returns `[1, '70']` then `[0, '70']` — the check-and-decrement is atomic,
  the key-value equivalent of `SELECT … FOR UPDATE` in Portfolio 3.
- **S10 Cache-aside:** dashboard 2.82 ms on a miss, 0.10 ms on a hit.
- **S11 Memory:** a latest-reading hash costs ~200 bytes; the whole demo dataset uses ~2.7 MB.

## 4. Performance — an honest comparison

| Test | Redis | PostgreSQL |
|---|---|---|
| Single client, one lookup (Python) | 93 µs | 70 µs |
| 32 Python clients, farm overview screen | 2,816 screens/s | 12,920 screens/s |
| 32 C clients, one lookup (`redis-benchmark` vs `pgbench`) | 103,466 req/s | 102,891 tps |
| Same, Redis pipelined ×8 | 588,235 req/s | — |

The expected "Redis is much faster" result **did not appear** for single lookups: with the right
index (Portfolio 2) PostgreSQL answered as fast as Redis on this 2-core machine. The Python test even
favoured PostgreSQL because it was client-bound (pure-Python driver, and two round trips per screen
in my Redis design vs one SQL query). Redis only pulled ahead with pipelining.

**Conclusion:** the case for Redis in AquaSense+ is not raw lookup speed. It is (1) **features**
the relational database lacks natively — streams with consumer groups, TTL expiry, pub/sub, atomic
Lua — and (2) **offloading**: every dashboard request Redis serves is load the primary database does
not carry, which matters most when thousands of farmers open the app at feeding time. A design
improvement found here: store a whole farm overview as one hash (`farm:{id}:overview`) so the screen
is a single round trip.

## 5. Document model (MongoDB) — comparison design

`example_batch_document.json` shows one production cycle as a single document: farm and pond details
embedded, daily records as an array, growth samples, harvest and summary. (Generated from the real
PostgreSQL data; MongoDB itself could not be installed in my environment, so `mongodb_design.js` is
a design script, not executed evidence.)

| Decision | Reason |
|---|---|
| **Embed** daily feeding/mortality in the batch | Bounded (~170 entries ≈ 15 KB) and always read with the batch → one read for the batch screen; `$push` + `$inc` is atomic on one document |
| **Do not embed** sensor readings | Unbounded growth would hit the 16 MB limit → separate time-series collection (bucket pattern) |
| **Duplicate** farm name/location into the batch | Faster reads; acceptable because farm names rarely change (trade-off: must update copies) |
| Partial unique index on `unit.unit_id` where `status: "active"` | Same "one active batch per pond" rule as Portfolio 1 |
| `$jsonSchema` validation | MongoDB is schema-flexible, so rules must be added deliberately |

## 6. Suitability summary — which model for which data

| Data | Best model | Why |
|---|---|---|
| Money, stock, harvests, sales | **Relational** (PostgreSQL) | ACID transactions across rows, constraints, joins for reports |
| Latest readings, alerts, OTP, cache, rankings | **Key-value** (Redis) | O(1) access, TTL, streams, pub/sub, sorted sets |
| Batch "record book" for offline-first mobile app | **Document** (MongoDB) | Matches the screen; one read/one atomic write; flexible extra fields per species |
| Raw sensor history | **Time-series / wide-column** (TimescaleDB, Cassandra) | Append-heavy, queried by pond + time range |
| Supply chain: hatchery → farm → trader → market | **Graph** (Neo4j) | Traceability queries ("which farms bought fingerlings from hatchery X that had a disease outbreak?") are path traversals |

AquaSense+ is therefore a **polyglot persistence** design: PostgreSQL stays the source of truth and
Redis sits in front of it for the real-time, short-lived and high-frequency data.
