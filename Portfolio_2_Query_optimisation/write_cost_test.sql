-- AquaSense+ | Portfolio 2 | What do the indexes cost on writes?
-- Inserts 100,000 sensor readings with and without the two water-quality
-- indexes, inside transactions that are rolled back (database unchanged).
\timing on
SET search_path TO aqua;

\echo '=== WITH ix_wqr_unit_time + ix_wqr_low_oxygen ==='
BEGIN;
INSERT INTO water_quality_reading (unit_id, recorded_at, ph, dissolved_oxygen_mgl, temperature_c, ammonia_mgl, source)
SELECT 1 + (g % 905), TIMESTAMPTZ '2026-11-04' + g * INTERVAL '1 second', 7.1, 1 + (g % 80) / 10.0, 28.0, 0.1, 'sensor'
FROM generate_series(1, 100000) g;
ROLLBACK;

\echo '=== WITHOUT the two indexes (dropped inside the transaction) ==='
BEGIN;
DROP INDEX ix_wqr_unit_time;
DROP INDEX ix_wqr_low_oxygen;
INSERT INTO water_quality_reading (unit_id, recorded_at, ph, dissolved_oxygen_mgl, temperature_c, ammonia_mgl, source)
SELECT 1 + (g % 905), TIMESTAMPTZ '2026-11-04' + g * INTERVAL '1 second', 7.1, 1 + (g % 80) / 10.0, 28.0, 0.1, 'sensor'
FROM generate_series(1, 100000) g;
ROLLBACK;   -- indexes come back because DDL is transactional in PostgreSQL
