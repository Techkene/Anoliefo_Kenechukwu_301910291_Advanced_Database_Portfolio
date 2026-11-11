-- =====================================================================
-- AquaSense+  |  Portfolio 3  |  Transactional business operations
-- Three operations where several rows must change together or not at all.
-- =====================================================================
SET search_path TO aqua;

-- ---------------------------------------------------------------------
-- 1) record_feeding: insert the feeding log AND reduce feed stock atomically.
--    FOR UPDATE locks the inventory row so two attendants recording at the
--    same time cannot both spend the same feed (prevents lost update).
-- ---------------------------------------------------------------------
CREATE OR REPLACE PROCEDURE record_feeding(p_batch INT, p_feed INT, p_qty NUMERIC, p_user INT)
LANGUAGE plpgsql AS $$
DECLARE
    v_farm  INT;
    v_stock NUMERIC;
BEGIN
    SELECT cu.farm_id INTO v_farm
    FROM stock_batch b JOIN culture_unit cu USING (unit_id)
    WHERE b.batch_id = p_batch AND b.status = 'active';
    IF v_farm IS NULL THEN
        RAISE EXCEPTION 'Batch % is not active', p_batch USING ERRCODE = 'AQ001';
    END IF;

    SELECT qty_kg INTO v_stock
    FROM feed_inventory WHERE farm_id = v_farm AND feed_id = p_feed
    FOR UPDATE;                                         -- row lock held until COMMIT/ROLLBACK

    IF v_stock IS NULL OR v_stock < p_qty THEN
        RAISE EXCEPTION 'Not enough feed: % kg in store, % kg requested', COALESCE(v_stock,0), p_qty
              USING ERRCODE = 'AQ002';
    END IF;

    INSERT INTO feeding_log (batch_id, feed_id, fed_on, qty_kg, recorded_by)
    VALUES (p_batch, p_feed, CURRENT_DATE, p_qty, p_user);

    UPDATE feed_inventory SET qty_kg = qty_kg - p_qty, updated_at = now()
    WHERE farm_id = v_farm AND feed_id = p_feed;
END $$;

-- ---------------------------------------------------------------------
-- 2) sell_fish: a sale may never exceed what is left of the harvest.
--    Locking the parent harvest row serialises competing buyers.
-- ---------------------------------------------------------------------
CREATE OR REPLACE FUNCTION sell_fish(p_harvest INT, p_trader INT, p_qty NUMERIC, p_price NUMERIC)
RETURNS INT LANGUAGE plpgsql AS $$
DECLARE
    v_total NUMERIC; v_sold NUMERIC; v_id INT;
BEGIN
    SELECT total_weight_kg INTO v_total FROM harvest WHERE harvest_id = p_harvest FOR UPDATE;
    SELECT COALESCE(SUM(qty_kg),0) INTO v_sold FROM sale WHERE harvest_id = p_harvest;
    IF v_sold + p_qty > v_total THEN
        RAISE EXCEPTION 'Oversell blocked: % kg harvested, % kg already sold, % kg requested',
              v_total, v_sold, p_qty USING ERRCODE = 'AQ003';
    END IF;
    INSERT INTO sale (harvest_id, trader_id, qty_kg, price_per_kg, sold_on)
    VALUES (p_harvest, p_trader, p_qty, p_price, CURRENT_DATE) RETURNING sale_id INTO v_id;
    RETURN v_id;
END $$;

-- ---------------------------------------------------------------------
-- 3) transfer_feed: cooperative members lend feed to each other.
--    Debit one farm, credit the other — the "bank transfer" of AquaSense+.
--    Rows are locked in ascending farm_id order to avoid deadlocks.
-- ---------------------------------------------------------------------
CREATE OR REPLACE PROCEDURE transfer_feed(p_from INT, p_to INT, p_feed INT, p_qty NUMERIC)
LANGUAGE plpgsql AS $$
BEGIN
    PERFORM 1 FROM feed_inventory
    WHERE feed_id = p_feed AND farm_id IN (p_from, p_to)
    ORDER BY farm_id
    FOR UPDATE;                                         -- consistent lock ordering

    UPDATE feed_inventory SET qty_kg = qty_kg - p_qty, updated_at = now()
    WHERE farm_id = p_from AND feed_id = p_feed;        -- CHECK (qty_kg >= 0) aborts if short

    INSERT INTO feed_inventory (farm_id, feed_id, qty_kg) VALUES (p_to, p_feed, p_qty)
    ON CONFLICT (farm_id, feed_id) DO UPDATE SET qty_kg = feed_inventory.qty_kg + EXCLUDED.qty_kg,
                                                 updated_at = now();
END $$;

-- Audit query used throughout the demos: feed in store must equal what
-- the logs say (only meaningful for the demo farm, which starts clean).
CREATE OR REPLACE VIEW v_feed_stock AS
SELECT farm_id, feed_id, qty_kg FROM feed_inventory;
