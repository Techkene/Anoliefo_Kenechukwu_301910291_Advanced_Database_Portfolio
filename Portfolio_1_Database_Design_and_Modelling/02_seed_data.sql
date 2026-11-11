-- =====================================================================
-- AquaSense+  |  Portfolio 1  |  Synthetic but realistic seed data
-- Deterministic: setseed() makes every run produce the same data.
-- Volumes (approx): 180 farms, ~900 culture units, ~3,000 batches,
-- ~450k feeding logs, ~1M water-quality readings.
-- Run AFTER 01_schema.sql:  psql -d aquasense -f 02_seed_data.sql
-- =====================================================================
SET search_path TO aqua;
SELECT setseed(0.8103);

-- ---------- reference data ----------
INSERT INTO state VALUES
 ('LA','Lagos','South West'),('OG','Ogun','South West'),('OY','Oyo','South West'),
 ('ON','Ondo','South West'),('DE','Delta','South South'),('RI','Rivers','South South'),
 ('ED','Edo','South South'),('AK','Akwa Ibom','South South'),('AN','Anambra','South East'),
 ('IM','Imo','South East'),('EN','Enugu','South East'),('FC','FCT Abuja','North Central'),
 ('NI','Niger','North Central'),('KW','Kwara','North Central'),('KD','Kaduna','North West'),
 ('KN','Kano','North West');

INSERT INTO species (common_name, scientific_name, target_weight_g, typical_cycle_days) VALUES
 ('African catfish', 'Clarias gariepinus',          1000, 150),
 ('Nile tilapia',    'Oreochromis niloticus',        450, 180),
 ('Heterotis',       'Heterotis niloticus',         2000, 300),
 ('Hybrid catfish',  'Heterobranchus x Clarias',    1200, 160);

INSERT INTO feed_product (brand, pellet_size_mm, protein_pct, bag_kg)
SELECT b.brand, s.size, CASE WHEN s.size = 2 THEN 45 WHEN s.size = 4 THEN 42 ELSE 38 END, 15
FROM (VALUES (1,'Coppens'),(2,'Aller Aqua'),(3,'Blue Crown'),(4,'Vital Feed')) b(i,brand)
CROSS JOIN (VALUES (1,2.0),(2,4.0),(3,6.0)) s(j,size)
ORDER BY b.i, s.j;                                  -- feed_id = (brand-1)*3 + size_index

-- ---------- cooperatives and users ----------
INSERT INTO cooperative (coop_name, state_code, registered_on)
SELECT c.name, c.st, DATE '2019-01-01' + (random()*1500)::int
FROM (VALUES ('Epe Fish Farmers Cooperative','LA'),('Ikorodu Aquaculture Union','LA'),
             ('Badagry Fish Growers','LA'),('Abeokuta Catfish Cluster','OG'),
             ('Ibadan Fish Farmers Association','OY'),('Warri Aqua Cooperative','DE'),
             ('Port Harcourt Fish Producers','RI'),('Benin Aquafarmers Union','ED'),
             ('Awka Catfish Network','AN'),('Owerri Fish Growers','IM'),
             ('Nsukka Aquaculture Co-op','EN'),('Abuja Fish Farmers Forum','FC'),
             ('Minna Aqua Cooperative','NI'),('Ilorin Fish Cluster','KW'),
             ('Kaduna Fish Growers Union','KD')) c(name, st);

-- name pools (Nigerian first and last names)
CREATE TEMP TABLE name_pool AS SELECT
 ARRAY['Adebayo','Chinedu','Ngozi','Fatima','Emeka','Funmilayo','Ibrahim','Aisha','Tunde','Chiamaka',
       'Yusuf','Blessing','Olumide','Halima','Ifeanyi','Kemi','Musa','Amaka','Segun','Zainab',
       'Obinna','Bisi','Abdullahi','Nkechi','Kunle','Hauwa','Uche','Folake','Sani','Ebere'] AS fn,
 ARRAY['Adeyemi','Okafor','Bello','Eze','Ogunleye','Nwosu','Abubakar','Olawale','Okonkwo','Danjuma',
       'Afolabi','Chukwu','Ibrahim','Oyelaran','Nnamdi','Lawal','Obi','Adesanya','Umar','Onyekachi'] AS ln;

-- role mix: 160 farmers, 360 farm staff, 60 traders, 15 coop admins, 10 extension officers
INSERT INTO app_user (full_name, phone, role, coop_id, preferred_language, created_at)
SELECT np.fn[1 + floor(random()*30)::int] || ' ' || np.ln[1 + floor(random()*20)::int],
       '+2348' || lpad((30000000 + g*7919)::text, 9, '0'),
       CASE WHEN g <= 160 THEN 'farmer' WHEN g <= 520 THEN 'farm_staff'
            WHEN g <= 580 THEN 'trader' WHEN g <= 595 THEN 'coop_admin'
            ELSE 'extension_officer' END,
       CASE WHEN g <= 160 AND random() < 0.7 THEN 1 + (g % 15)
            WHEN g BETWEEN 581 AND 595 THEN g - 580 END,
       (ARRAY['en','en','en','pcm','yo','ig','ha'])[1 + floor(random()*7)::int],
       TIMESTAMPTZ '2024-06-01' + random() * INTERVAL '450 days'
FROM generate_series(1, 605) g CROSS JOIN name_pool np;

-- ---------- farms (farmers 1..160; first 20 own a second farm) ----------
CREATE TEMP TABLE lga(st, name, lat, lon) AS VALUES
 ('LA','Epe',6.583,3.983),('LA','Ikorodu',6.619,3.506),('LA','Badagry',6.415,2.881),
 ('OG','Abeokuta North',7.161,3.348),('OG','Ijebu Ode',6.820,3.920),
 ('OY','Ibadan North',7.396,3.917),('OY','Oyo East',7.846,3.931),('ON','Akure South',7.250,5.195),
 ('DE','Warri South',5.517,5.750),('DE','Ughelli North',5.490,5.990),('RI','Obio-Akpor',4.850,7.010),
 ('ED','Oredo',6.338,5.625),('AK','Uyo',5.038,7.909),('AN','Awka South',6.210,7.072),
 ('IM','Owerri Municipal',5.485,7.035),('EN','Nsukka',6.857,7.396),('FC','Kuje',8.879,7.227),
 ('NI','Chanchaga',9.614,6.556),('KW','Ilorin West',8.496,4.542),('KD','Chikun',10.380,7.300),
 ('KN','Kumbotso',11.900,8.500);

INSERT INTO farm (owner_id, farm_name, state_code, lga, latitude, longitude, created_at)
SELECT o.owner,
       split_part(u.full_name,' ',2) || CASE WHEN o.k = 2 THEN ' Fish Farm II' ELSE ' Fish Farm' END,
       l.st, l.name,
       round((l.lat + (random()-0.5)*0.2)::numeric, 5),
       round((l.lon + (random()-0.5)*0.2)::numeric, 5),
       u.created_at + INTERVAL '3 days'
FROM (SELECT g AS owner, 1 AS k FROM generate_series(1,160) g
      UNION ALL SELECT g, 2 FROM generate_series(1,20) g) o
JOIN app_user u ON u.user_id = o.owner
CROSS JOIN LATERAL (SELECT * FROM lga OFFSET (o.owner * 5 + o.k * 3) % 21 LIMIT 1) l;

-- staff: two per farm (manager + attendant), users 161..520
INSERT INTO farm_staff (farm_id, user_id, staff_role, joined_on)
SELECT f.farm_id, 160 + f.farm_id,       'manager',   (f.created_at + INTERVAL '10 days')::date FROM farm f
UNION ALL
SELECT f.farm_id, 160 + 180 + f.farm_id, 'attendant', (f.created_at + INTERVAL '20 days')::date FROM farm f;

-- ---------- culture units: 2–8 per farm, ~25% sensor-equipped ----------
INSERT INTO culture_unit (farm_id, unit_code, unit_type, volume_m3, has_sensor)
SELECT f.farm_id,
       CASE t.typ WHEN 'cage' THEN 'CAGE-' WHEN 'earthen_pond' THEN 'POND-' ELSE 'TANK-' END || lpad(n::text,2,'0'),
       t.typ,
       CASE t.typ WHEN 'earthen_pond' THEN 300 + round((random()*900)::numeric,0)
                  WHEN 'cage' THEN 50 + round((random()*100)::numeric,0)
                  ELSE 8 + round((random()*20)::numeric,1) END,
       random() < 0.25
FROM farm f
CROSS JOIN LATERAL generate_series(1, 2 + (f.farm_id * 13) % 7) n
CROSS JOIN LATERAL (SELECT (ARRAY['earthen_pond','earthen_pond','concrete_tank','concrete_tank',
                                  'tarpaulin_tank','cage','ras_tank'])[1 + (f.farm_id + n) % 7] AS typ) t;

-- ---------- stock batches: sequential cycles per unit, Nov 2024 → Nov 2026 ----------
INSERT INTO stock_batch (unit_id, species_id, stocked_on, initial_count, initial_avg_weight_g,
                         fingerling_unit_cost, hatchery_source, status, closed_on)
SELECT u.unit_id, sp.species_id, s.stocked_on,
       CASE u.unit_type WHEN 'earthen_pond' THEN 3000 + (random()*7000)::int
                        WHEN 'cage' THEN 2000 + (random()*3000)::int
                        ELSE 500 + (random()*1500)::int END,
       round((3 + random()*7)::numeric, 2),
       round((40 + random()*40)::numeric, 2),
       (ARRAY['Durante Fish Industries','CEFAS Hatchery','Agro-Allied Hatchery Ibadan',
              'Oyo Fingerlings Ltd','In-house hatchery'])[1 + floor(random()*5)::int],
       CASE WHEN s.stocked_on + 160 < DATE '2026-11-01' THEN 'harvested' ELSE 'active' END,
       CASE WHEN s.stocked_on + 160 < DATE '2026-11-01' THEN s.stocked_on + 140 + (random()*20)::int END
FROM culture_unit u
CROSS JOIN LATERAL generate_series(0, 4) k
-- each pond starts on its own date (staggered over ~4 months) so harvests spread across the year
CROSS JOIN LATERAL (SELECT DATE '2024-11-01' + ((u.unit_id*37) % 120) + k*175 + ((u.unit_id*31 + k*17) % 11) AS stocked_on) s
CROSS JOIN LATERAL (SELECT CASE WHEN (u.unit_id + k) % 20 < 14 THEN 1
                                WHEN (u.unit_id + k) % 20 < 18 THEN 2
                                WHEN (u.unit_id + k) % 20 < 19 THEN 4 ELSE 3 END AS species_id) sp
WHERE s.stocked_on <= DATE '2026-10-25';

-- ---------- daily feeding logs (≈95% of days recorded) ----------
INSERT INTO feeding_log (batch_id, feed_id, fed_on, qty_kg, recorded_by, recorded_at)
SELECT b.batch_id,
       ((cu.farm_id % 4) * 3) + CASE WHEN w.wg < 50 THEN 1 WHEN w.wg < 250 THEN 2 ELSE 3 END,
       d::date,
       LEAST(500, GREATEST(0.2, round((b.initial_count * w.wg * 0.025 / 1000)::numeric, 2))),
       340 + cu.farm_id,                                   -- the farm's attendant
       d + INTERVAL '8 hours' + random() * INTERVAL '3 hours'
FROM stock_batch b
JOIN culture_unit cu ON cu.unit_id = b.unit_id
JOIN species sp ON sp.species_id = b.species_id
CROSS JOIN LATERAL generate_series(b.stocked_on, COALESCE(b.closed_on, DATE '2026-11-03'), INTERVAL '1 day') d
CROSS JOIN LATERAL (SELECT b.initial_avg_weight_g +
        (sp.target_weight_g - b.initial_avg_weight_g) *
        power(LEAST(1.0, (d::date - b.stocked_on)::numeric / sp.typical_cycle_days), 1.6) AS wg) w
WHERE random() < 0.95;

-- ---------- mortality (≈8% of days) ----------
INSERT INTO mortality_log (batch_id, recorded_on, dead_count, suspected_cause, recorded_by)
SELECT b.batch_id, d::date,
       GREATEST(1, (b.initial_count * random() * 0.03)::int),
       (ARRAY['low_oxygen','disease','cannibalism','predator','handling','unknown','unknown'])[1 + floor(random()*7)::int],
       340 + cu.farm_id
FROM stock_batch b
JOIN culture_unit cu ON cu.unit_id = b.unit_id
CROSS JOIN LATERAL generate_series(b.stocked_on + 1, COALESCE(b.closed_on, DATE '2026-11-03'), INTERVAL '1 day') d
WHERE random() < 0.08;

-- ---------- growth sampling every 14 days ----------
INSERT INTO growth_sample (batch_id, sampled_on, sample_size, avg_weight_g)
SELECT b.batch_id, d::date, 20 + (random()*30)::int,
       round((b.initial_avg_weight_g + (sp.target_weight_g - b.initial_avg_weight_g) *
             power(LEAST(1.0,(d::date - b.stocked_on)::numeric / sp.typical_cycle_days),1.6)
             * (0.85 + random()*0.3))::numeric, 1)
FROM stock_batch b JOIN species sp USING (species_id)
CROSS JOIN LATERAL generate_series(b.stocked_on + 14, COALESCE(b.closed_on, DATE '2026-11-03'), INTERVAL '14 days') d;

-- ---------- water quality: manual daily at 07:00, sensors every 2 hours (2026) ----------
INSERT INTO water_quality_reading (unit_id, recorded_at, ph, dissolved_oxygen_mgl, temperature_c, ammonia_mgl, source)
SELECT cu.unit_id, ts,
       round((7.2 + (random()-0.5)*1.2)::numeric, 2),
       round(GREATEST(0.8, 5.0 + 2.2*sin(2*pi()*(extract(hour from ts) - 9)/24) + (random()-0.5)*1.5)::numeric, 2),
       round((28.5 + 2*sin(2*pi()*(extract(hour from ts) - 9)/24) + (random()-0.5))::numeric, 1),
       round((CASE WHEN random() < 0.05 THEN 0.8 + random()*1.2 ELSE 0.02 + random()*0.4 END)::numeric, 3),
       CASE WHEN cu.has_sensor THEN 'sensor' ELSE 'manual' END
FROM culture_unit cu
CROSS JOIN LATERAL generate_series(TIMESTAMPTZ '2026-01-01 07:00+01', TIMESTAMPTZ '2026-11-03 23:00+01',
        CASE WHEN cu.has_sensor THEN INTERVAL '2 hours' ELSE INTERVAL '1 day' END) ts;

-- ---------- harvests for closed batches (count reconciled with mortality) ----------
INSERT INTO harvest (batch_id, harvested_on, fish_count, total_weight_kg, is_partial)
SELECT b.batch_id, b.closed_on,
       GREATEST(1, ((b.initial_count - COALESCE(m.dead,0)) * (0.93 + random()*0.06))::int),
       round((GREATEST(1, (b.initial_count - COALESCE(m.dead,0)) * 0.95)
              * sp.target_weight_g * (0.8 + random()*0.35) / 1000)::numeric, 2),
       FALSE
FROM stock_batch b
JOIN species sp USING (species_id)
LEFT JOIN (SELECT batch_id, SUM(dead_count) dead FROM mortality_log GROUP BY batch_id) m USING (batch_id)
WHERE b.status = 'harvested';

-- ---------- sales: each harvest sold in 1–4 lots ----------
INSERT INTO sale (harvest_id, trader_id, buyer_name, qty_kg, price_per_kg, sold_on, payment_status)
SELECT h.harvest_id,
       CASE WHEN random() < 0.65 THEN 521 + floor(random()*60)::int END,
       'Walk-in buyer',
       round(h.total_weight_kg / n.lots, 2),
       round((2300 + random()*1300 + CASE WHEN h.harvested_on >= DATE '2026-01-01' THEN 300 ELSE 0 END)::numeric, -1),
       h.harvested_on + (i - 1),
       (ARRAY['paid','paid','paid','part','pending'])[1 + floor(random()*5)::int]
FROM harvest h
CROSS JOIN LATERAL (SELECT 1 + (h.harvest_id % 4) AS lots) n
CROSS JOIN LATERAL generate_series(1, n.lots) i;

-- ---------- feed purchases (monthly) and current inventory ----------
INSERT INTO feed_purchase (farm_id, feed_id, bags, price_per_bag, purchased_on)
SELECT f.farm_id, ((f.farm_id % 4) * 3) + s, 5 + (random()*40)::int,
       round((CASE s WHEN 1 THEN 32000 WHEN 2 THEN 26000 ELSE 24000 END * (0.95 + random()*0.15)
              * CASE WHEN m >= DATE '2026-01-01' THEN 1.12 ELSE 1 END)::numeric, -2),
       m::date + (random()*25)::int
FROM farm f
CROSS JOIN generate_series(DATE '2025-01-01', DATE '2026-10-01', INTERVAL '1 month') m
CROSS JOIN generate_series(1,3) s;

INSERT INTO feed_inventory (farm_id, feed_id, qty_kg)
SELECT f.farm_id, ((f.farm_id % 4) * 3) + s, round((random()*600)::numeric, 2)
FROM farm f CROSS JOIN generate_series(1,3) s;

-- ---------- expenses (monthly overheads) ----------
INSERT INTO expense (farm_id, category, amount, spent_on, note)
SELECT f.farm_id, c.cat,
       round((c.base * (0.7 + random()*0.6))::numeric, -2),
       m::date + (random()*27)::int, NULL
FROM farm f
CROSS JOIN generate_series(DATE '2025-01-01', DATE '2026-10-01', INTERVAL '1 month') m
CROSS JOIN (VALUES ('labour',120000),('fuel',45000),('electricity',25000),
                   ('drugs',15000),('transport',20000)) c(cat, base)
WHERE random() < 0.9;

ANALYZE;

-- quick row-count summary
SELECT 'farm' t, count(*) FROM farm UNION ALL
SELECT 'culture_unit', count(*) FROM culture_unit UNION ALL
SELECT 'stock_batch', count(*) FROM stock_batch UNION ALL
SELECT 'feeding_log', count(*) FROM feeding_log UNION ALL
SELECT 'water_quality_reading', count(*) FROM water_quality_reading UNION ALL
SELECT 'mortality_log', count(*) FROM mortality_log UNION ALL
SELECT 'harvest', count(*) FROM harvest UNION ALL
SELECT 'sale', count(*) FROM sale UNION ALL
SELECT 'expense', count(*) FROM expense;
