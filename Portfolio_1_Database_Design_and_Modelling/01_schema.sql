-- =====================================================================
-- AquaSense+  |  MIT 8103 Advanced Database Systems  |  Portfolio 1
-- Relational schema (PostgreSQL 16) — normalised to 3NF / BCNF
-- Run:  psql -d aquasense -f 01_schema.sql
-- =====================================================================

DROP SCHEMA IF EXISTS aqua CASCADE;
CREATE SCHEMA aqua;
SET search_path TO aqua;

-- ---------------------------------------------------------------------
-- 1. Reference / lookup tables
-- ---------------------------------------------------------------------
CREATE TABLE state (
    state_code   CHAR(2)      PRIMARY KEY,              -- e.g. 'LA' Lagos
    state_name   VARCHAR(40)  NOT NULL UNIQUE,
    geo_zone     VARCHAR(20)  NOT NULL
        CHECK (geo_zone IN ('South West','South South','South East',
                            'North Central','North West','North East'))
);

CREATE TABLE species (
    species_id        SMALLSERIAL  PRIMARY KEY,
    common_name       VARCHAR(60)  NOT NULL UNIQUE,      -- e.g. African catfish
    scientific_name   VARCHAR(80)  NOT NULL UNIQUE,      -- e.g. Clarias gariepinus
    target_weight_g   NUMERIC(7,1) NOT NULL CHECK (target_weight_g > 0),
    typical_cycle_days SMALLINT    NOT NULL CHECK (typical_cycle_days BETWEEN 30 AND 400)
);

CREATE TABLE feed_product (
    feed_id         SERIAL       PRIMARY KEY,
    brand           VARCHAR(60)  NOT NULL,
    pellet_size_mm  NUMERIC(4,1) NOT NULL CHECK (pellet_size_mm > 0),
    protein_pct     NUMERIC(4,1) NOT NULL CHECK (protein_pct BETWEEN 10 AND 70),
    bag_kg          NUMERIC(5,1) NOT NULL DEFAULT 15 CHECK (bag_kg > 0),
    UNIQUE (brand, pellet_size_mm)
);

-- ---------------------------------------------------------------------
-- 2. People and organisations
-- ---------------------------------------------------------------------
CREATE TABLE cooperative (
    coop_id      SERIAL       PRIMARY KEY,
    coop_name    VARCHAR(120) NOT NULL UNIQUE,
    state_code   CHAR(2)      NOT NULL REFERENCES state(state_code),
    registered_on DATE        NOT NULL DEFAULT CURRENT_DATE
);

CREATE TABLE app_user (
    user_id      SERIAL       PRIMARY KEY,
    full_name    VARCHAR(100) NOT NULL,
    phone        VARCHAR(15)  NOT NULL UNIQUE
        CHECK (phone ~ '^\+234[0-9]{10}$'),              -- Nigerian MSISDN
    role         VARCHAR(20)  NOT NULL
        CHECK (role IN ('farmer','farm_staff','trader','coop_admin','extension_officer')),
    coop_id      INT          REFERENCES cooperative(coop_id) ON DELETE SET NULL,
    preferred_language VARCHAR(10) NOT NULL DEFAULT 'en'
        CHECK (preferred_language IN ('en','yo','ig','ha','pcm')),
    created_at   TIMESTAMPTZ  NOT NULL DEFAULT now()
);

CREATE TABLE farm (
    farm_id      SERIAL       PRIMARY KEY,
    owner_id     INT          NOT NULL REFERENCES app_user(user_id),
    farm_name    VARCHAR(120) NOT NULL,
    state_code   CHAR(2)      NOT NULL REFERENCES state(state_code),
    lga          VARCHAR(60)  NOT NULL,
    latitude     NUMERIC(8,5) CHECK (latitude  BETWEEN 4 AND 14),   -- Nigeria bounds
    longitude    NUMERIC(8,5) CHECK (longitude BETWEEN 2.5 AND 15),
    created_at   TIMESTAMPTZ  NOT NULL DEFAULT now(),
    UNIQUE (owner_id, farm_name)
);

-- M:N  — staff can work on several farms, a farm has several staff
CREATE TABLE farm_staff (
    farm_id      INT          NOT NULL REFERENCES farm(farm_id) ON DELETE CASCADE,
    user_id      INT          NOT NULL REFERENCES app_user(user_id) ON DELETE CASCADE,
    staff_role   VARCHAR(20)  NOT NULL CHECK (staff_role IN ('manager','attendant','security')),
    joined_on    DATE         NOT NULL DEFAULT CURRENT_DATE,
    PRIMARY KEY (farm_id, user_id)
);

-- ---------------------------------------------------------------------
-- 3. Production structure
-- ---------------------------------------------------------------------
CREATE TABLE culture_unit (                                -- a pond, tank or cage
    unit_id      SERIAL       PRIMARY KEY,
    farm_id      INT          NOT NULL REFERENCES farm(farm_id) ON DELETE CASCADE,
    unit_code    VARCHAR(20)  NOT NULL,                     -- e.g. 'POND-03'
    unit_type    VARCHAR(20)  NOT NULL
        CHECK (unit_type IN ('earthen_pond','concrete_tank','tarpaulin_tank','cage','ras_tank')),
    volume_m3    NUMERIC(8,2) NOT NULL CHECK (volume_m3 > 0),
    has_sensor   BOOLEAN      NOT NULL DEFAULT FALSE,
    is_active    BOOLEAN      NOT NULL DEFAULT TRUE,
    UNIQUE (farm_id, unit_code)
);

CREATE TABLE stock_batch (                                 -- one production cycle
    batch_id         SERIAL       PRIMARY KEY,
    unit_id          INT          NOT NULL REFERENCES culture_unit(unit_id),
    species_id       SMALLINT     NOT NULL REFERENCES species(species_id),
    stocked_on       DATE         NOT NULL,
    initial_count    INT          NOT NULL CHECK (initial_count > 0),
    initial_avg_weight_g NUMERIC(7,2) NOT NULL CHECK (initial_avg_weight_g > 0),
    fingerling_unit_cost NUMERIC(8,2) NOT NULL CHECK (fingerling_unit_cost >= 0),  -- NGN
    hatchery_source  VARCHAR(120),
    status           VARCHAR(10)  NOT NULL DEFAULT 'active'
        CHECK (status IN ('active','harvested','closed')),
    closed_on        DATE,
    CHECK (closed_on IS NULL OR closed_on >= stocked_on),
    CHECK ((status = 'active') = (closed_on IS NULL))
);
-- Business rule: a culture unit holds at most ONE active batch at a time
CREATE UNIQUE INDEX uq_one_active_batch_per_unit
    ON stock_batch(unit_id) WHERE status = 'active';

-- ---------------------------------------------------------------------
-- 4. Daily operational records (the high-volume tables)
-- ---------------------------------------------------------------------
CREATE TABLE feeding_log (
    feeding_id   BIGSERIAL    PRIMARY KEY,
    batch_id     INT          NOT NULL REFERENCES stock_batch(batch_id) ON DELETE CASCADE,
    feed_id      INT          NOT NULL REFERENCES feed_product(feed_id),
    fed_on       DATE         NOT NULL,
    qty_kg       NUMERIC(7,2) NOT NULL CHECK (qty_kg > 0 AND qty_kg <= 500),
    recorded_by  INT          NOT NULL REFERENCES app_user(user_id),
    recorded_at  TIMESTAMPTZ  NOT NULL DEFAULT now()
);

CREATE TABLE water_quality_reading (
    reading_id       BIGSERIAL    PRIMARY KEY,
    unit_id          INT          NOT NULL REFERENCES culture_unit(unit_id) ON DELETE CASCADE,
    recorded_at      TIMESTAMPTZ  NOT NULL,
    ph               NUMERIC(4,2) CHECK (ph BETWEEN 0 AND 14),
    dissolved_oxygen_mgl NUMERIC(5,2) CHECK (dissolved_oxygen_mgl >= 0),
    temperature_c    NUMERIC(4,1) CHECK (temperature_c BETWEEN 0 AND 50),
    ammonia_mgl      NUMERIC(5,3) CHECK (ammonia_mgl >= 0),
    source           VARCHAR(6)   NOT NULL DEFAULT 'manual' CHECK (source IN ('manual','sensor'))
);

CREATE TABLE mortality_log (
    mortality_id BIGSERIAL    PRIMARY KEY,
    batch_id     INT          NOT NULL REFERENCES stock_batch(batch_id) ON DELETE CASCADE,
    recorded_on  DATE         NOT NULL,
    dead_count   INT          NOT NULL CHECK (dead_count > 0),
    suspected_cause VARCHAR(20) NOT NULL DEFAULT 'unknown'
        CHECK (suspected_cause IN ('low_oxygen','disease','cannibalism','predator','handling','theft','unknown')),
    recorded_by  INT          NOT NULL REFERENCES app_user(user_id)
);

CREATE TABLE growth_sample (                               -- periodic weighing
    sample_id    BIGSERIAL    PRIMARY KEY,
    batch_id     INT          NOT NULL REFERENCES stock_batch(batch_id) ON DELETE CASCADE,
    sampled_on   DATE         NOT NULL,
    sample_size  SMALLINT     NOT NULL CHECK (sample_size > 0),
    avg_weight_g NUMERIC(7,1) NOT NULL CHECK (avg_weight_g > 0),
    UNIQUE (batch_id, sampled_on)
);

-- ---------------------------------------------------------------------
-- 5. Inventory, harvest, sales, expenses (the money side)
-- ---------------------------------------------------------------------
CREATE TABLE feed_inventory (                              -- current balance per farm per feed
    farm_id      INT          NOT NULL REFERENCES farm(farm_id) ON DELETE CASCADE,
    feed_id      INT          NOT NULL REFERENCES feed_product(feed_id),
    qty_kg       NUMERIC(10,2) NOT NULL CHECK (qty_kg >= 0),   -- can never go negative
    updated_at   TIMESTAMPTZ  NOT NULL DEFAULT now(),
    PRIMARY KEY (farm_id, feed_id)
);

CREATE TABLE feed_purchase (
    purchase_id  BIGSERIAL    PRIMARY KEY,
    farm_id      INT          NOT NULL REFERENCES farm(farm_id) ON DELETE CASCADE,
    feed_id      INT          NOT NULL REFERENCES feed_product(feed_id),
    bags         INT          NOT NULL CHECK (bags > 0),
    price_per_bag NUMERIC(10,2) NOT NULL CHECK (price_per_bag > 0),   -- NGN
    purchased_on DATE         NOT NULL
);

CREATE TABLE harvest (
    harvest_id     SERIAL       PRIMARY KEY,
    batch_id       INT          NOT NULL REFERENCES stock_batch(batch_id),
    harvested_on   DATE         NOT NULL,
    fish_count     INT          NOT NULL CHECK (fish_count > 0),
    total_weight_kg NUMERIC(9,2) NOT NULL CHECK (total_weight_kg > 0),
    is_partial     BOOLEAN      NOT NULL DEFAULT FALSE
);

CREATE TABLE sale (
    sale_id        SERIAL       PRIMARY KEY,
    harvest_id     INT          NOT NULL REFERENCES harvest(harvest_id),
    trader_id      INT          REFERENCES app_user(user_id),   -- NULL = walk-in buyer
    buyer_name     VARCHAR(100),
    qty_kg         NUMERIC(9,2) NOT NULL CHECK (qty_kg > 0),
    price_per_kg   NUMERIC(8,2) NOT NULL CHECK (price_per_kg > 0),  -- NGN
    sold_on        DATE         NOT NULL,
    payment_status VARCHAR(8)   NOT NULL DEFAULT 'pending'
        CHECK (payment_status IN ('pending','part','paid')),
    CHECK (trader_id IS NOT NULL OR buyer_name IS NOT NULL)
);

CREATE TABLE expense (
    expense_id   BIGSERIAL    PRIMARY KEY,
    farm_id      INT          NOT NULL REFERENCES farm(farm_id) ON DELETE CASCADE,
    batch_id     INT          REFERENCES stock_batch(batch_id),   -- NULL = farm overhead
    category     VARCHAR(20)  NOT NULL
        CHECK (category IN ('labour','fuel','electricity','drugs','water','repairs','transport','other')),
    amount       NUMERIC(12,2) NOT NULL CHECK (amount > 0),     -- NGN
    spent_on     DATE         NOT NULL,
    note         VARCHAR(200)
);

-- ---------------------------------------------------------------------
-- 6. Derived data is computed, not stored (avoids update anomalies)
-- ---------------------------------------------------------------------
CREATE VIEW v_batch_status AS
SELECT b.batch_id,
       b.unit_id,
       b.species_id,
       b.stocked_on,
       b.initial_count,
       COALESCE(m.dead, 0)       AS total_dead,
       COALESCE(h.harvested, 0)  AS total_harvested,
       b.initial_count - COALESCE(m.dead,0) - COALESCE(h.harvested,0) AS fish_remaining,
       ROUND(100.0 * (b.initial_count - COALESCE(m.dead,0)) / b.initial_count, 1) AS survival_pct,
       COALESCE(f.feed_kg, 0)    AS total_feed_kg
FROM stock_batch b
LEFT JOIN (SELECT batch_id, SUM(dead_count)  dead      FROM mortality_log GROUP BY batch_id) m USING (batch_id)
LEFT JOIN (SELECT batch_id, SUM(fish_count)  harvested FROM harvest       GROUP BY batch_id) h USING (batch_id)
LEFT JOIN (SELECT batch_id, SUM(qty_kg)      feed_kg   FROM feeding_log   GROUP BY batch_id) f USING (batch_id);

COMMENT ON VIEW v_batch_status IS
  'Fish remaining, survival % and total feed are derived on read so they can never drift out of sync with the logs.';
