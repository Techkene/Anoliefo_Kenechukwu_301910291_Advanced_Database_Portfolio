# Anoliefo_Kenechukwu_301910291_Portfolio 3 — Transactions and Concurrency

AquaSense+ is used by several people on the same farm at the same time: two attendants may record
feeding from the same feed store, two traders may try to buy from the same harvest, and cooperative
members lend feed to each other. Each of these must be **atomic** (all steps or none) and safe
under **concurrency**.

## Files

| File | Purpose |
|---|---|
| `01_transaction_procedures.sql` | `record_feeding` (log + stock deduction), `sell_fish` (no overselling), `transfer_feed` (debit/credit between farms) |
| `02_commit_rollback_savepoint.sql` | COMMIT, automatic ROLLBACK, explicit ROLLBACK, SAVEPOINT (offline sync), oversell guard, atomic transfer |
| `03_concurrency_demos.py` | Two live sessions racing each other: lost update, `FOR UPDATE`, REPEATABLE READ retry, non-repeatable read, write skew, SERIALIZABLE, deadlock and its fix |
| `evidence/commit_rollback_savepoint_output.txt` | psql output of file 02 |
| `evidence/concurrency_demo_output.txt` | Timestamped interleaving of every concurrency demo |

**Run:**
```bash
psql -d aquasense -f 01_transaction_procedures.sql
psql -d aquasense -f 02_commit_rollback_savepoint.sql
python3 03_concurrency_demos.py
```

## Part A — ACID in single transactions (`02_commit_rollback_savepoint.sql`)

| # | Scenario | What happened | ACID property shown |
|---|---|---|---|
| 1 | Record 30 kg feed | Log inserted **and** stock 100 → 70 kg in one COMMIT | Atomicity, Durability |
| 2 | Record 500 kg with only 70 kg in store | Procedure raises `AQ002`; the `COMMIT` becomes `ROLLBACK`; stock stays 70, no log row | Atomicity, Consistency |
| 3 | Attendant records 20 kg then cancels | Inside the transaction stock showed 50; after `ROLLBACK` it is 70 again | Atomicity |
| 4 | Offline sync of 4 queued records, one with a typo (pH 19.5) | `ROLLBACK TO SAVEPOINT rec3` discards only the bad reading; the other 3 records commit | Partial rollback |
| 5 | Sell 99,999 kg from a 1,319 kg harvest | `sell_fish` blocks it (`AQ003`) | Consistency (business rule) |
| 6 | Farm 1 lends 25 kg to farm 5 | 35 + 25 = 60 kg before and after; an over-large transfer hits `CHECK (qty_kg >= 0)` and nothing moves | Atomicity, Consistency |

Custom errors use their own SQLSTATE class (`AQ001`–`AQ003`) so the mobile app can tell a
business-rule failure apart from a database error. (A first version used `P0002`/`P0003`, which
turned out to be PL/pgSQL's reserved `no_data_found`/`too_many_rows` codes — found when the Python
driver mapped the error to the wrong exception class.)

Note in the output: the rejected pH row was given `reading_id` 1233614 even though earlier test
inserts were rolled back. **Sequences are not transactional** — gaps in IDs are normal and must
never be used to detect "missing" records.

## Part B — Concurrency problems and controls (`03_concurrency_demos.py`)

All timings and interleavings below are from my own run on Windows (PostgreSQL 16), saved in
`evidence/concurrency_demo_output.txt` and in `portfolio3_run.ipynb`.

| Demo | Problem / mechanism | Result |
|---|---|---|
| **D1** Lost update | Both attendants read 100 kg, app computes new value, both write. READ COMMITTED does not stop this. | Final 80 kg instead of 50 — **30 kg of feed vanished from the books** |
| **D2** `SELECT … FOR UPDATE` (pessimistic) | B's read **waited 1.19 s** on A's row lock. `pg_locks` captured B waiting for a `ShareLock` on A's transaction (`pg_blocking_pids` = A's pid). | 50 kg ✔ |
| **D3** REPEATABLE READ (optimistic) | B's update failed with **40001 "could not serialize access due to concurrent update"**; the app rolled back and retried with a fresh snapshot | 50 kg ✔ after 1 retry |
| **D4** Non-repeatable read | Under READ COMMITTED, A read 100 then 250 in the same transaction; under REPEATABLE READ, A read 100 both times (one snapshot) | Shows why reports should run in REPEATABLE READ |
| **D5** Write skew (phantom) | Both traders check "sold so far = 0" and **insert** a 600 kg sale. No row is updated twice, so REPEATABLE READ allows it | **1,200 kg sold from a 1,000 kg harvest** |
| **D5** SERIALIZABLE | PostgreSQL's SSI detected the read/write dependency: B got **40001 "read/write dependencies among transactions"** | 600 kg ✔ |
| **D5b** `sell_fish()` in READ COMMITTED | Locks the parent `harvest` row first, so buyers queue; B then sees 600 kg sold and gets `AQ003` | 600 kg ✔ |
| **D6** Deadlock | A locks farm 1 then wants farm 5; B locks farm 5 then wants farm 1. After `deadlock_timeout` (1 s) PostgreSQL aborted A with **40P01 deadlock detected** | One transaction lost its work |
| **D6b** Lock ordering | `transfer_feed()` locks both rows in ascending `farm_id` order, so B **queued for 0.80 s** instead of deadlocking | Both committed; total stays 200 kg ✔ |

## Design decisions for AquaSense+

1. **Default isolation stays READ COMMITTED** (PostgreSQL default) because most writes are simple
   inserts of daily records that do not conflict.
2. **Stock-changing operations use explicit row locks** (`record_feeding`, `transfer_feed`,
   `sell_fish`). Locks are short (a few ms) and conflicts are rare (same farm, same feed, same
   second), so pessimistic locking is simpler for the app than handling retries everywhere.
3. **Lock ordering** is enforced inside the procedure, not left to the app, so a deadlock between
   two transfers cannot happen however the app calls it.
4. **Reports** (cooperative dashboards, end-of-cycle profit) run in **REPEATABLE READ** so every
   figure comes from the same snapshot.
5. **SERIALIZABLE is the safety net** for complex rules that cannot be expressed as one lock; the
   app must then retry on SQLSTATE `40001`. The cost is extra aborts under contention.
6. **The app must never do read → compute → write** for balances (D1). It either calls the
   procedure or uses a relative update (`SET qty_kg = qty_kg - 30`).