# Portfolio 2 — Query Processing and Optimisation

All work runs against the AquaSense+ database from Portfolio 1 (~1.6 million rows; the
`water_quality_reading` table alone is ~1.03 million rows / 113 MB).

## Files

| File | Purpose |
|---|---|
| `queries.sql` | Eight real AquaSense+ screens/reports (Q1–Q8) |
| `indexes.sql` | The optimisation indexes, each tied to the query it serves |
| `run_benchmark.py` | Drops the indexes, times every query (median of 6 warm runs), captures `EXPLAIN (ANALYZE, BUFFERS)`, adds the indexes, repeats, then tests a materialized view |
| `plans/Qn_before.txt`, `plans/Qn_after.txt` | Full execution plans for every query, before and after |
| `results/Qn_result.txt` | Query output (first 15 rows) |
| `performance_results.md`, `performance_chart.png` | Generated summary table and chart |
| `write_cost_test.sql`, `results/write_cost_output.txt` | Measures what the indexes cost on inserts |

**Run:** `PGHOST=… PGUSER=… python3 run_benchmark.py` (needs `psycopg2-binary`, `matplotlib`).
Timings will differ on another machine; the plan shapes and buffer counts are what matter. The results below are from my own run on my Windows laptop (PostgreSQL 16); the buffer counts were identical to an earlier run on a Linux machine, while the timings were slower in absolute terms.

## The starting point

PostgreSQL creates indexes for primary keys and `UNIQUE` constraints, **but not for foreign-key
columns**. So after Portfolio 1, every lookup such as "readings for pond 316" or "feeding logs for
batch 2041" had to read the whole table.

## Results

![Before vs after](performance_chart.png)

| Query | Business purpose | Before | After | Pages read (8 kB buffers) | What changed in the plan |
|---|---|---|---|---|---|
| Q1 | Pond dashboard — last 7 days of readings for one pond | 207.95 ms | 0.92 ms | 11,674 → 5 | Seq Scan → Index Scan on `ix_wqr_unit_time` |
| Q2 | Latest reading for every pond on a farm (`LATERAL … LIMIT 1`) | 1,586.47 ms | 0.64 ms | 92,803 → 35 | One full scan *per pond* → one index probe per pond, read newest-first so `LIMIT 1` stops immediately |
| Q3 | Low-oxygen alert feed (DO < 2.5 mg/L, last 24 h, Lagos) | 332.28 ms | 1.24 ms | 11,799 → 15 | Seq Scan → **Index Only Scan** on the *partial* index `ix_wqr_low_oxygen` |
| Q4 | Batch report: feed used, harvest weight, FCR | 2,273.99 ms | 2.98 ms | 110,160 → 222 | Repeated full scans of `feeding_log` → **Index Only Scan** on covering index (`Heap Fetches: 0`) |
| Q5 | Mortality spike detection (window function, 7-day moving average) | 7.02 ms | 4.93 ms | 386 → 118 | Seq Scan → Index Only Scan on `ix_mortality_batch_date` |
| Q6 | Readings on 1 Oct for one pond — `date(recorded_at) = …` | 171.12 ms | 5.75 ms | 11,600 → 59 | Index used **only for `unit_id`**; the date is a `Filter` that throws away every other day's rows |
| Q7 | Same question written as a range `recorded_at >= … AND < …` | 224.97 ms | 0.48 ms | 11,600 → 4 | Both columns used in `Index Cond`; reads only that day |
| Q8 | Cooperative dashboard — monthly revenue per state | 18.30 ms | 19.54 ms | 154 → 154 | No real change — intentionally (see below); the small difference is run-to-run noise. Materialized view: **0.93 ms** |

(Exact figures: [`performance_results.md`](performance_results.md); full plans in [`plans/`](plans/).)

## Analysis — why each technique worked

**1. Composite index column order matters (Q1, Q2, Q7).** `ix_wqr_unit_time (unit_id, recorded_at DESC)`
puts the equality column first and the range/sort column second. The planner can jump to pond 316,
then walk its readings in time order. That also removes the `Sort` node from Q1 and lets Q2's
`LIMIT 1` finish after reading a single index entry per pond. Q2 shows the biggest gain (≈2,500×, from 1.6 seconds to 0.64 ms)
because without the index the nested loop re-scanned the 1M-row table once for every pond.

**2. Partial indexes for rare, important rows (Q3).** Only ~3% of readings are below 2.5 mg/L, so
`ix_wqr_low_oxygen` is 1.4 MB instead of ~31 MB. With `INCLUDE (unit_id, dissolved_oxygen_mgl)` the
alert query never visits the table at all (Index Only Scan). For AquaSense+ this is the query that
must be fastest — a fish kill from low oxygen can happen within hours at night.

**3. Covering indexes (Q4, Q5).** `feeding_log (batch_id) INCLUDE (qty_kg)` holds everything
`SUM(qty_kg)` needs. The plan shows `Heap Fetches: 0`: 26 batches × ~145 feeding rows were summed
from 222 pages instead of 110,160 — 2.3 seconds became 3 ms.

**4. Sargability — write predicates the index can use (Q6 vs Q7).** Wrapping the column in a
function (`date(recorded_at AT TIME ZONE …)`) hides it from the index. Even with the right index in
place, Q6 must read *all* of pond 316's readings and filter them (59 pages, `Rows Removed by Filter`),
while Q7's half-open range reads 4 pages — about 12× faster (5.75 ms vs 0.48 ms) for identical results. The fix costs
nothing: rewrite the query, no new index.

**5. Knowing when NOT to index (Q8).** The dashboard aggregates most rows of five small tables
(`sale` is only ~7,500 rows), so a hash join over sequential scans is already the cheapest plan and
the planner correctly ignores the extra indexes. The right tool is a **materialized view**
(`mv_state_monthly_sales`): reading it takes 0.93 ms instead of 19.54 ms, and `REFRESH MATERIALIZED VIEW CONCURRENTLY`
(needs the unique index on `(state_code, month)`) takes about 101 ms and does not block readers. Cooperative
reports only need nightly freshness, so a scheduled refresh is acceptable.

## The cost side — indexes are not free

| Test (100,000 sensor inserts, `write_cost_test.sql`) | Time |
|---|---|
| With `ix_wqr_unit_time` + `ix_wqr_low_oxygen` | 2,528 ms |
| Without them | 2,047 ms |

The two indexes add about 24% to insert time and ~32 MB of storage. For AquaSense+ that trade is worth it:
sensor ponds write one reading every 2 hours, but farmers open the pond dashboard and alerts many
times a day, and the alert query is safety-critical. If sensor frequency grew to every minute,
the next steps would be batching inserts, time-based partitioning (Portfolio 5) and BRIN indexes on
`recorded_at`.

## How the planner chose (reading the plans)
- **Cost estimates vs actual:** e.g. Q4-after estimates 27 rows and returns 26, so the statistics
  from `ANALYZE` are accurate; bad estimates would be the first thing to check if a plan looked wrong.
- **Join choice:** small dimension (`culture_unit` for one farm, 8 rows) → Hash Join; per-batch
  aggregates → Nested Loop with an index probe each loop (`loops=26`).
- **Seq Scan is not always bad:** `stock_batch` (3,764 rows, 47 pages) stays a Seq Scan after
  indexing because reading 47 pages sequentially is cheaper than random index lookups.