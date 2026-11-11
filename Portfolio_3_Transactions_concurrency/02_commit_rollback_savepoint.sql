-- =====================================================================
-- AquaSense+  |  Portfolio 3  |  COMMIT, ROLLBACK and SAVEPOINT
-- Uses a dedicated demo farm (farm 1) and active batch so results are clear.
-- Run: psql -d aquasense -f 02_commit_rollback_savepoint.sql
-- =====================================================================
SET search_path TO aqua;
\set ON_ERROR_STOP off

-- demo setup: farm 1 gets exactly 100 kg of feed 4 (Aller Aqua 2 mm)
INSERT INTO feed_inventory VALUES (1, 4, 100, now())
ON CONFLICT (farm_id, feed_id) DO UPDATE SET qty_kg = 100;
SELECT b.batch_id AS demo_batch FROM stock_batch b JOIN culture_unit cu USING (unit_id)
WHERE cu.farm_id = 1 AND b.status = 'active' ORDER BY batch_id LIMIT 1 \gset
\echo 'Demo batch:' :demo_batch
SELECT count(*) AS logs_before FROM feeding_log WHERE batch_id = :demo_batch \gset

\echo ''
\echo '=== 1. COMMIT: record 30 kg of feed — log inserted AND stock reduced together ==='
BEGIN;
CALL record_feeding(:demo_batch, 4, 30, 341);
COMMIT;
SELECT qty_kg AS stock_after_commit FROM feed_inventory WHERE farm_id = 1 AND feed_id = 4;
SELECT count(*) - :logs_before AS new_logs FROM feeding_log WHERE batch_id = :demo_batch;

\echo ''
\echo '=== 2. AUTOMATIC ROLLBACK: try to feed 500 kg when only 70 kg is in store ==='
BEGIN;
CALL record_feeding(:demo_batch, 4, 500, 341);     -- raises AQ002 (custom error class)
COMMIT;                                              -- PostgreSQL turns this into ROLLBACK
SELECT qty_kg AS stock_unchanged FROM feed_inventory WHERE farm_id = 1 AND feed_id = 4;
SELECT count(*) - :logs_before AS new_logs_still FROM feeding_log WHERE batch_id = :demo_batch;

\echo ''
\echo '=== 3. EXPLICIT ROLLBACK: attendant records 20 kg, then cancels (wrong pond) ==='
BEGIN;
CALL record_feeding(:demo_batch, 4, 20, 341);
SELECT qty_kg AS stock_inside_txn FROM feed_inventory WHERE farm_id = 1 AND feed_id = 4;
ROLLBACK;
SELECT qty_kg AS stock_after_rollback FROM feed_inventory WHERE farm_id = 1 AND feed_id = 4;

\echo ''
\echo '=== 4. SAVEPOINT: offline sync of 4 queued records — one bad record must not lose the rest ==='
-- When a farmer reconnects, the app uploads everything recorded offline in one transaction.
BEGIN;
SAVEPOINT rec1;  CALL record_feeding(:demo_batch, 4, 10, 341);
SAVEPOINT rec2;  INSERT INTO mortality_log (batch_id, recorded_on, dead_count, suspected_cause, recorded_by)
                 VALUES (:demo_batch, CURRENT_DATE, 12, 'low_oxygen', 341);
SAVEPOINT rec3;  INSERT INTO water_quality_reading (unit_id, recorded_at, ph) VALUES (1, now(), 19.5);  -- typo: pH 19.5
ROLLBACK TO SAVEPOINT rec3;                          -- discard only the bad record
SAVEPOINT rec4;  INSERT INTO water_quality_reading (unit_id, recorded_at, ph, dissolved_oxygen_mgl)
                 VALUES (1, now(), 7.5, 4.8);
COMMIT;
SELECT qty_kg AS stock_after_sync FROM feed_inventory WHERE farm_id = 1 AND feed_id = 4;
SELECT count(*) AS mortality_rows_today FROM mortality_log WHERE batch_id = :demo_batch AND recorded_on = CURRENT_DATE;
SELECT count(*) AS bad_ph_rows FROM water_quality_reading WHERE ph > 14;

\echo ''
\echo '=== 5. Oversell protection: harvest sale larger than what is left ==='
SELECT harvest_id, total_weight_kg, (SELECT SUM(qty_kg) FROM sale s WHERE s.harvest_id = h.harvest_id) AS sold
FROM harvest h ORDER BY harvest_id LIMIT 1;
BEGIN;
SELECT sell_fish(1, 525, 99999, 3200);
ROLLBACK;

\echo ''
\echo '=== 6. ATOMIC TRANSFER: farm 1 lends 25 kg to farm 5 — total feed is conserved ==='
INSERT INTO feed_inventory VALUES (5, 4, 0, now()) ON CONFLICT (farm_id, feed_id) DO UPDATE SET qty_kg = 0;
SELECT SUM(qty_kg) AS total_before FROM feed_inventory WHERE feed_id = 4 AND farm_id IN (1,5);
CALL transfer_feed(1, 5, 4, 25);
SELECT farm_id, qty_kg FROM feed_inventory WHERE feed_id = 4 AND farm_id IN (1,5) ORDER BY farm_id;
SELECT SUM(qty_kg) AS total_after FROM feed_inventory WHERE feed_id = 4 AND farm_id IN (1,5);
\echo 'Transfer more than farm 1 holds (fails, nothing moves):'
CALL transfer_feed(1, 5, 4, 1000);
SELECT farm_id, qty_kg FROM feed_inventory WHERE feed_id = 4 AND farm_id IN (1,5) ORDER BY farm_id;
