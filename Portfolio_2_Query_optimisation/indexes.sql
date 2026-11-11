-- =====================================================================
-- AquaSense+  |  Portfolio 2  |  Optimisation indexes
-- PostgreSQL does NOT index foreign-key columns automatically, so the
-- high-volume log tables start with only their primary key.
-- =====================================================================
SET search_path TO aqua;

-- Q1, Q2, Q7: composite index matching "WHERE unit_id = ? ORDER BY recorded_at DESC"
CREATE INDEX IF NOT EXISTS ix_wqr_unit_time
    ON water_quality_reading (unit_id, recorded_at DESC);

-- Q3: partial index — only the ~3% of readings that are alarms are indexed,
-- so the index is small and the alert feed reads only alarm rows
CREATE INDEX IF NOT EXISTS ix_wqr_low_oxygen
    ON water_quality_reading (recorded_at)
    INCLUDE (unit_id, dissolved_oxygen_mgl)
    WHERE dissolved_oxygen_mgl < 2.5;

-- Q4: covering index — INCLUDE (qty_kg) lets SUM(qty_kg) be answered by an index-only scan
CREATE INDEX IF NOT EXISTS ix_feeding_batch_cov
    ON feeding_log (batch_id) INCLUDE (qty_kg);

-- Q4: FK lookups
CREATE INDEX IF NOT EXISTS ix_harvest_batch   ON harvest (batch_id);
CREATE INDEX IF NOT EXISTS ix_batch_unit      ON stock_batch (unit_id);
CREATE INDEX IF NOT EXISTS ix_unit_farm       ON culture_unit (farm_id);

-- Q5: mortality by batch and date (supports the window's PARTITION BY / ORDER BY)
CREATE INDEX IF NOT EXISTS ix_mortality_batch_date
    ON mortality_log (batch_id, recorded_on) INCLUDE (dead_count);

-- Q8: deliberately NOT indexed further — see README (small tables, aggregate
-- over most rows; a materialized view is the right tool instead)

ANALYZE;
