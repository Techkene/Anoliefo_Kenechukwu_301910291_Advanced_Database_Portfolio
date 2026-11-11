-- =====================================================================
-- AquaSense+  |  Portfolio 1  |  Proving the constraints actually work
-- Each test is wrapped in a SAVEPOINT so a failure does not abort the run.
-- Run: psql -d aquasense -f 03_constraint_tests.sql
-- =====================================================================
SET search_path TO aqua;
\set ON_ERROR_STOP off
BEGIN;

\echo '--- T1  CHECK: feed stock can never go negative ---'
SAVEPOINT t1;
UPDATE feed_inventory SET qty_kg = qty_kg - 999999 WHERE farm_id = 1 AND feed_id = 4;
ROLLBACK TO SAVEPOINT t1;

\echo '--- T2  PARTIAL UNIQUE INDEX: only one ACTIVE batch per pond ---'
SAVEPOINT t2;
INSERT INTO stock_batch (unit_id, species_id, stocked_on, initial_count, initial_avg_weight_g, fingerling_unit_cost)
SELECT unit_id, 1, CURRENT_DATE, 1000, 5, 50 FROM stock_batch WHERE status = 'active' LIMIT 1;
ROLLBACK TO SAVEPOINT t2;

\echo '--- T3  FOREIGN KEY: feeding log for a batch that does not exist ---'
SAVEPOINT t3;
INSERT INTO feeding_log (batch_id, feed_id, fed_on, qty_kg, recorded_by) VALUES (999999, 1, CURRENT_DATE, 10, 341);
ROLLBACK TO SAVEPOINT t3;

\echo '--- T4  CHECK (regex): phone must be a valid +234 number ---'
SAVEPOINT t4;
INSERT INTO app_user (full_name, phone, role) VALUES ('Test User', '08031234567', 'farmer');
ROLLBACK TO SAVEPOINT t4;

\echo '--- T5  CHECK: pH must be between 0 and 14 ---'
SAVEPOINT t5;
INSERT INTO water_quality_reading (unit_id, recorded_at, ph) VALUES (1, now(), 15.2);
ROLLBACK TO SAVEPOINT t5;

\echo '--- T6  UNIQUE: duplicate unit code on the same farm ---'
SAVEPOINT t6;
INSERT INTO culture_unit (farm_id, unit_code, unit_type, volume_m3)
SELECT farm_id, unit_code, unit_type, volume_m3 FROM culture_unit WHERE unit_id = 1;
ROLLBACK TO SAVEPOINT t6;

\echo '--- T7  CHECK: an active batch cannot have a closed_on date ---'
SAVEPOINT t7;
UPDATE stock_batch SET closed_on = CURRENT_DATE WHERE batch_id = (SELECT min(batch_id) FROM stock_batch WHERE status='active');
ROLLBACK TO SAVEPOINT t7;

\echo '--- T8  VALID insert succeeds (control case) ---'
SAVEPOINT t8;
INSERT INTO water_quality_reading (unit_id, recorded_at, ph, dissolved_oxygen_mgl, temperature_c, ammonia_mgl)
VALUES (1, now(), 7.10, 5.40, 28.3, 0.050) RETURNING reading_id, ph, dissolved_oxygen_mgl;
ROLLBACK TO SAVEPOINT t8;

ROLLBACK;   -- leave the database exactly as it was
\echo 'All constraint tests finished — database unchanged.'
