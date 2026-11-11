# Kenechukwu Anoliefo 301910291 Portfolio 1 — Database Design and Modelling

## 1. Case study: AquaSense+

| Item | Description |
|---|---|
| **Organisation / system** | **AquaSense+** — a mobile and web record-keeping platform for fish farmers in Nigeria (www.aquasenseapp.com). It helps farmers, traders and cooperatives plan production, keep daily farm records and see profit per production cycle. |
| **Nature of the business** | Aquaculture (mainly African catfish and tilapia) in earthen ponds, concrete/tarpaulin tanks, cages and RAS tanks. A farm runs several ponds; each pond goes through stocking → daily feeding and checks → harvest → sale. |
| **Users of the database** | Farm owners (farmers), farm staff (managers and attendants who enter daily records), fish traders/buyers, cooperative administrators, and agricultural extension officers. |
| **Major data stored** | Farms and their location; ponds/tanks/cages (culture units); stocking batches; daily feeding, mortality and growth-sampling records; water-quality readings (manual and from IoT sensors); feed products, purchases and current feed stock; harvests, sales and expenses. |
| **Key operations** | Stock a pond; record daily feeding (which also reduces feed stock); log water quality and deaths; check fish remaining and survival rate; harvest and sell fish; track feed stock and expenses; compute feed conversion ratio (FCR) and profit per batch; give cooperatives a regional view. |

The same case study is used in all five portfolios.

## 2. Files

| File | Purpose |
|---|---|
| `01_schema.sql` | Full DDL: 18 tables, keys, constraints, partial unique index, derived-data view |
| `02_seed_data.sql` | Deterministic synthetic data (`setseed`) — ~1.6 million rows |
| `03_constraint_tests.sql` | Deliberately breaks each rule to prove the constraints are enforced |
| `Anoliefo_Kenechukwu_301910291_erd.png` / `Anoliefo_Kenechukwu_301910291_erd.svg` / `Anoliefo_Kenechukwu_301910291_erd.dot` | ER diagram (Graphviz source included so it can be regenerated) |
| `evidence/` | Row counts, constraint-test output, table sizes, `\d` output |

**How to run** (PostgreSQL 16):
```bash
createdb aquasense
psql -d aquasense -f 01_schema.sql
psql -d aquasense -f 02_seed_data.sql      # ~30 s
psql -d aquasense -f 03_constraint_tests.sql
```

## 3. ER diagram

![AquaSense+ ERD](Anoliefo_Kenechukwu_301910291_erd.png)

**Main entities and relationships**
- A **farm** is owned by one **app_user** (farmer) and has many **culture units** (ponds/tanks/cages).
- **farm_staff** resolves the many-to-many between users and farms (a manager can work on several farms).
- A **culture unit** has many **stock batches** over time, but **at most one active batch** at a time.
- A **stock batch** has many **feeding**, **mortality** and **growth-sample** records, and one or more **harvests**; each **harvest** is sold in one or more **sales**, optionally to a registered **trader**.
- **Water-quality readings** belong to the culture unit (not the batch), because the water is measured even when a pond is empty or being prepared.
- **feed_inventory** holds the current stock of each feed product per farm (composite key `farm_id, feed_id`).

## 4. Relational schema (keys)

PK = primary key, FK = foreign key, UQ = unique

```
state(state_code PK, state_name UQ, geo_zone)
species(species_id PK, common_name UQ, scientific_name UQ, target_weight_g, typical_cycle_days)
feed_product(feed_id PK, brand, pellet_size_mm, protein_pct, bag_kg)          UQ(brand, pellet_size_mm)
cooperative(coop_id PK, coop_name UQ, state_code FK→state, registered_on)
app_user(user_id PK, full_name, phone UQ, role, coop_id FK→cooperative, preferred_language, created_at)
farm(farm_id PK, owner_id FK→app_user, farm_name, state_code FK→state, lga, latitude, longitude)  UQ(owner_id, farm_name)
farm_staff(farm_id PK/FK→farm, user_id PK/FK→app_user, staff_role, joined_on)
culture_unit(unit_id PK, farm_id FK→farm, unit_code, unit_type, volume_m3, has_sensor, is_active)  UQ(farm_id, unit_code)
stock_batch(batch_id PK, unit_id FK, species_id FK, stocked_on, initial_count, initial_avg_weight_g,
            fingerling_unit_cost, hatchery_source, status, closed_on)        partial UQ(unit_id) WHERE status='active'
feeding_log(feeding_id PK, batch_id FK, feed_id FK, fed_on, qty_kg, recorded_by FK→app_user, recorded_at)
mortality_log(mortality_id PK, batch_id FK, recorded_on, dead_count, suspected_cause, recorded_by FK)
growth_sample(sample_id PK, batch_id FK, sampled_on, sample_size, avg_weight_g)  UQ(batch_id, sampled_on)
water_quality_reading(reading_id PK, unit_id FK, recorded_at, ph, dissolved_oxygen_mgl, temperature_c, ammonia_mgl, source)
feed_inventory(farm_id PK/FK, feed_id PK/FK, qty_kg, updated_at)
feed_purchase(purchase_id PK, farm_id FK, feed_id FK, bags, price_per_bag, purchased_on)
harvest(harvest_id PK, batch_id FK, harvested_on, fish_count, total_weight_kg, is_partial)
sale(sale_id PK, harvest_id FK, trader_id FK→app_user (nullable), buyer_name, qty_kg, price_per_kg, sold_on, payment_status)
expense(expense_id PK, farm_id FK, batch_id FK (nullable), category, amount, spent_on, note)
```

## 5. Constraints and why each one exists

| Constraint | Business rule it protects | Proven in |
|---|---|---|
| `CHECK (qty_kg >= 0)` on `feed_inventory` | A farm cannot feed fish with feed it does not have | T1 |
| Partial unique index `uq_one_active_batch_per_unit` | A pond cannot hold two active production cycles — otherwise feed and deaths would be booked to the wrong batch | T2 |
| Foreign keys (with `ON DELETE CASCADE` only for owned child records) | No orphan records: every log belongs to a real batch/pond | T3 |
| `CHECK (phone ~ '^\+234[0-9]{10}$')` | Phone is the login ID and SMS channel, so it must be a valid Nigerian number | T4 |
| Range `CHECK`s on pH (0–14), temperature, oxygen, ammonia | Stops impossible sensor or typing errors from triggering false alerts | T5 |
| `UNIQUE (farm_id, unit_code)` | Staff refer to "POND-03"; the code must be unambiguous within a farm | T6 |
| `CHECK ((status='active') = (closed_on IS NULL))` | Status and closing date can never contradict each other | T7 |
| Enumerated `CHECK`s on `role`, `unit_type`, `suspected_cause`, `payment_status` | Keeps reporting categories consistent (lookup-style without extra joins) | schema |
| `NUMERIC` for money and weights (never `FLOAT`) | Naira amounts and kg must not suffer floating-point rounding | schema |

Output of the tests: [`evidence/constraint_tests_output.txt`](evidence/constraint_tests_output.txt) — all 7 invalid operations are rejected and the valid control insert succeeds.

## 6. Normalisation

### Starting point — the paper "daily pond record" (UNF)
Before AquaSense+, a typical farm record looks like one row per pond per day, with repeating groups:

```
FarmName, FarmLGA, OwnerName, OwnerPhone, PondCode, PondType, Species, StockDate, FishStocked,
Date, {FeedBrand, PelletSize, Protein%, KgFed}, {DeadCount, Cause}, pH, DO, Temp, RecordedBy
```

Problems: repeating groups (a pond may get two feed types in a day); the owner's phone repeats on every row (update anomaly); a feed brand's protein % cannot be stored until it is used (insertion anomaly); deleting the last record of a pond loses the pond itself (deletion anomaly).

### 1NF — atomic values, no repeating groups
Each feeding event and each mortality event becomes its own row; every column holds one value. Key becomes (PondCode, Date, FeedBrand, PelletSize…), which is long and still mixes facts about different things.

### 2NF — remove partial dependencies
With a composite key such as `(farm, pond_code, date, feed_brand, pellet_size)`:
- `protein_pct` depends only on `(feed_brand, pellet_size)` → moved to **feed_product**.
- `pond_type`, `volume` depend only on `(farm, pond_code)` → moved to **culture_unit**.
- `species`, `stock_date`, `fish_stocked` depend only on the production cycle → moved to **stock_batch**.
- pH/DO/temperature depend on `(pond, time)` → moved to **water_quality_reading**.

### 3NF — remove transitive dependencies
- `owner_phone` depends on `owner`, not on the farm → **app_user** holds it; **farm** keeps only `owner_id`.
- `state_name` / `geo_zone` depend on `state_code` → **state** table.
- `scientific_name`, `target_weight_g` depend on species → **species** table.

### BCNF check
In every table, every determinant is a candidate key. The two tables with composite keys were checked specifically:
- `farm_staff(farm_id, user_id → staff_role, joined_on)` — the only determinant is the full key.
- `feed_inventory(farm_id, feed_id → qty_kg, updated_at)` — same.
- `culture_unit` has two candidate keys (`unit_id` and `(farm_id, unit_code)`); both determine all attributes, so it is in BCNF.

### Deliberate design decisions (not mistakes)
1. **Derived values are not stored.** Fish remaining, survival % and total feed are computed by the view `v_batch_status`. Storing a "fish_remaining" column would create an update anomaly every time a death is logged.
2. **`feed_inventory` is a controlled exception.** Technically the balance could be derived as *purchases − feed used*, but recomputing over 500k feeding logs on every screen load is too slow for a phone on a weak network. The balance is stored and kept correct by **transactions** (see Portfolio 3), with a `CHECK` so it can never go negative.
3. **Surrogate keys** (`SERIAL`/`BIGSERIAL`) are used for high-volume tables because natural keys (e.g. pond code + date + feed) are long and would bloat every index and foreign key. Natural uniqueness is still enforced with `UNIQUE` constraints.
4. **Water quality is linked to the unit, not the batch**, because readings continue between cycles (pond preparation, liming).
