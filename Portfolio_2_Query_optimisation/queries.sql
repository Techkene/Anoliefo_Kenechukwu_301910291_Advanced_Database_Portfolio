-- =====================================================================
-- AquaSense+  |  Portfolio 2  |  Business queries used for optimisation
-- Each query is marked "-- @Qn title" so run_benchmark.py can find it.
-- Fixed reference time (2026-11-03 23:59 WAT) instead of now() so the
-- results are reproducible.
-- =====================================================================

-- @Q1 Pond dashboard: last 7 days of water readings for one pond
SELECT recorded_at, ph, dissolved_oxygen_mgl, temperature_c, ammonia_mgl
FROM aqua.water_quality_reading
WHERE unit_id = 316
  AND recorded_at >= TIMESTAMPTZ '2026-11-03 23:59+01' - INTERVAL '7 days'
ORDER BY recorded_at DESC;

-- @Q2 Farm overview: latest reading for every pond on a farm
SELECT cu.unit_code, r.recorded_at, r.dissolved_oxygen_mgl, r.ph, r.temperature_c
FROM aqua.culture_unit cu
CROSS JOIN LATERAL (
    SELECT recorded_at, dissolved_oxygen_mgl, ph, temperature_c
    FROM aqua.water_quality_reading w
    WHERE w.unit_id = cu.unit_id
    ORDER BY recorded_at DESC
    LIMIT 1) r
WHERE cu.farm_id = 64
ORDER BY cu.unit_code;

-- @Q3 Alert feed: dangerously low oxygen (< 2.5 mg/L) in Lagos in the last 24 hours
SELECT f.farm_name, cu.unit_code, w.recorded_at, w.dissolved_oxygen_mgl
FROM aqua.water_quality_reading w
JOIN aqua.culture_unit cu ON cu.unit_id = w.unit_id
JOIN aqua.farm f          ON f.farm_id  = cu.farm_id
WHERE w.dissolved_oxygen_mgl < 2.5
  AND w.recorded_at >= TIMESTAMPTZ '2026-11-03 23:59+01' - INTERVAL '24 hours'
  AND f.state_code = 'LA'
ORDER BY w.dissolved_oxygen_mgl;

-- @Q4 Batch report: feed used, harvest weight and FCR for every closed batch on one farm
SELECT b.batch_id, cu.unit_code, b.stocked_on, b.closed_on,
       fl.feed_kg,
       h.harvest_kg,
       ROUND(fl.feed_kg / NULLIF(h.harvest_kg - b.initial_count * b.initial_avg_weight_g / 1000, 0), 2) AS fcr
FROM aqua.stock_batch b
JOIN aqua.culture_unit cu ON cu.unit_id = b.unit_id
JOIN LATERAL (SELECT SUM(qty_kg) AS feed_kg   FROM aqua.feeding_log fl WHERE fl.batch_id = b.batch_id) fl ON TRUE
JOIN LATERAL (SELECT SUM(total_weight_kg) AS harvest_kg FROM aqua.harvest h WHERE h.batch_id = b.batch_id) h ON TRUE
WHERE cu.farm_id = 64 AND b.status = 'harvested'
ORDER BY b.stocked_on;

-- @Q5 Mortality spike detection: days where deaths > 3x the batch's 7-day average (one farm)
WITH daily AS (
    SELECT m.batch_id, m.recorded_on, SUM(m.dead_count) AS dead
    FROM aqua.mortality_log m
    JOIN aqua.stock_batch b ON b.batch_id = m.batch_id
    JOIN aqua.culture_unit cu ON cu.unit_id = b.unit_id
    WHERE cu.farm_id = 64
    GROUP BY m.batch_id, m.recorded_on
), windowed AS (
    SELECT *, AVG(dead) OVER (PARTITION BY batch_id ORDER BY recorded_on
                              ROWS BETWEEN 7 PRECEDING AND 1 PRECEDING) AS avg_prev7
    FROM daily
)
SELECT batch_id, recorded_on, dead, ROUND(avg_prev7, 1) AS avg_prev7
FROM windowed
WHERE avg_prev7 IS NOT NULL AND dead > 3 * avg_prev7
ORDER BY recorded_on DESC;

-- @Q6 Sargability: all readings on 1 Oct 2026 for one pond (BAD form — function on the column)
SELECT count(*), ROUND(AVG(dissolved_oxygen_mgl), 2) AS avg_do
FROM aqua.water_quality_reading
WHERE unit_id = 316
  AND date(recorded_at AT TIME ZONE 'Africa/Lagos') = DATE '2026-10-01';

-- @Q7 Sargability: same question rewritten as a range (GOOD form — index-friendly)
SELECT count(*), ROUND(AVG(dissolved_oxygen_mgl), 2) AS avg_do
FROM aqua.water_quality_reading
WHERE unit_id = 316
  AND recorded_at >= TIMESTAMPTZ '2026-10-01 00:00+01'
  AND recorded_at <  TIMESTAMPTZ '2026-10-02 00:00+01';

-- @Q8 Cooperative dashboard: monthly revenue and kg sold per state (2026)
SELECT f.state_code, date_trunc('month', s.sold_on)::date AS month,
       ROUND(SUM(s.qty_kg))                AS kg_sold,
       ROUND(SUM(s.qty_kg * s.price_per_kg)) AS revenue_ngn
FROM aqua.sale s
JOIN aqua.harvest h       ON h.harvest_id = s.harvest_id
JOIN aqua.stock_batch b   ON b.batch_id   = h.batch_id
JOIN aqua.culture_unit cu ON cu.unit_id   = b.unit_id
JOIN aqua.farm f          ON f.farm_id    = cu.farm_id
WHERE s.sold_on >= DATE '2026-01-01'
GROUP BY f.state_code, month
ORDER BY f.state_code, month;
