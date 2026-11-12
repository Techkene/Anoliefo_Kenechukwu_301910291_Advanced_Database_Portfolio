"""
AquaSense+ | Portfolio 4 | NoSQL implementation with Redis 7 (key-value store)

Loads data from the Portfolio-1 PostgreSQL database into Redis structures that
match how the mobile app reads it, then demonstrates each access pattern.

  S1  Load: latest-reading hashes, time-window sorted sets, farm geo index
  S2  Key-value lookup vs the equivalent SQL (latency over 2,000 calls)
  S3  Sorted set as a time-series window (last 24 h of dissolved oxygen)
  S4  Stream + consumer group: live sensor ingestion -> alerts -> write-behind to PostgreSQL
  S5  Idempotent offline sync with SET NX
  S6  Phone-OTP login codes with TTL (automatic expiry)
  S7  Cooperative leaderboard (sorted set) of feed conversion ratio
  S8  Geo search: a trader in Epe finds nearby farms with fish ready to sell
  S9  Atomic Lua script: decrement feed stock only if enough is left
  S10 Cache-aside for the cooperative dashboard
  S11 Memory and persistence evidence

Usage: python3 redis_aquasense.py | tee evidence/redis_demo_output.txt
"""
import os, time, json, statistics, datetime as dt
import psycopg2, redis

pg = psycopg2.connect(dbname=os.getenv("PGDATABASE", "aquasense")); pg.autocommit = True
cur = pg.cursor(); cur.execute("SET search_path TO aqua")
REDIS = dict(host=os.getenv("REDIS_HOST", "127.0.0.1"), port=int(os.getenv("REDIS_PORT", "6379")),
             password=os.getenv("REDIS_PASSWORD") or None, decode_responses=True)
r = redis.Redis(**REDIS)
print(f"Connected to {r.info('server').get('redis_version', '?')} "
      f"({'Memurai' if 'memurai' in str(r.info('server')).lower() else 'Redis'}) on {REDIS['host']}:{REDIS['port']}")

# Apply the durability and memory settings from config/redis-aquasense.conf at runtime
# (so the same settings hold on Linux Redis and on Memurai for Windows)
for k, v in {"appendonly": "yes", "appendfsync": "everysec", "maxmemory": "256mb",
             "maxmemory-policy": "volatile-lru"}.items():
    try:
        r.config_set(k, v)
    except redis.ResponseError as e:
        print(f"  (could not set {k}: {e})")

def h(t): print("\n" + "=" * 100 + f"\n{t}\n" + "=" * 100, flush=True)
def cmd(c, res): print(f"redis> {c}\n       {res}", flush=True)
def epoch(ts): return int(ts.timestamp())

r.flushdb()

# ---------------------------------------------------------------------------
h("S1  LOAD from PostgreSQL into Redis structures")
t = time.perf_counter()
cur.execute("""SELECT DISTINCT ON (w.unit_id) w.unit_id, cu.farm_id, cu.unit_code, w.recorded_at,
                      w.ph, w.dissolved_oxygen_mgl, w.temperature_c, w.ammonia_mgl
               FROM water_quality_reading w JOIN culture_unit cu USING (unit_id)
               ORDER BY w.unit_id, w.recorded_at DESC""")
pipe = r.pipeline(transaction=False)
for u, farm, code, ts, ph, do, temp, nh3 in cur.fetchall():
    pipe.hset(f"pond:{u}:latest", mapping={"farm_id": farm, "unit_code": code, "ts": ts.isoformat(),
              "ph": str(ph), "do": str(do), "temp": str(temp), "nh3": str(nh3)})
    pipe.sadd(f"farm:{farm}:ponds", u)
pipe.execute()

cur.execute("""SELECT unit_id, recorded_at, dissolved_oxygen_mgl FROM water_quality_reading
               WHERE recorded_at >= TIMESTAMPTZ '2026-11-03 23:59+01' - INTERVAL '7 days'""")
pipe = r.pipeline(transaction=False); n_ts = 0
for u, ts, do in cur.fetchall():
    pipe.zadd(f"pond:{u}:do", {f"{epoch(ts)}:{do}": epoch(ts)}); n_ts += 1
pipe.execute()

cur.execute("SELECT farm_id, farm_name, state_code, lga, longitude, latitude FROM farm")
pipe = r.pipeline(transaction=False)
for fid, name, st, lga, lon, lat in cur.fetchall():
    pipe.hset(f"farm:{fid}", mapping={"name": name, "state": st, "lga": lga})
    pipe.geoadd("farms:geo", (float(lon), float(lat), f"farm:{fid}"))
pipe.execute()
print(f"Loaded {len(r.keys('pond:*:latest'))} latest-reading hashes, {n_ts:,} time-window entries, "
      f"{r.zcard('farms:geo')} geo-indexed farms in {(time.perf_counter()-t):.2f}s. DBSIZE = {r.dbsize()}")

# ---------------------------------------------------------------------------
h("S2  KEY-VALUE LOOKUP — pond dashboard header (latest reading of pond 316)")
cmd("HGETALL pond:316:latest", r.hgetall("pond:316:latest"))
sql = ("SELECT recorded_at, ph, dissolved_oxygen_mgl, temperature_c, ammonia_mgl FROM water_quality_reading "
       "WHERE unit_id = 316 ORDER BY recorded_at DESC LIMIT 1")
def bench(fn, n=2000):
    ts = []
    for _ in range(n):
        t = time.perf_counter(); fn(); ts.append((time.perf_counter() - t) * 1e6)
    return statistics.median(ts)
redis_us = bench(lambda: r.hgetall("pond:316:latest"))
pg_us = bench(lambda: (cur.execute(sql), cur.fetchone()))
print(f"Single client, median of 2,000 calls: Redis HGETALL = {redis_us:.0f} µs | PostgreSQL (indexed) = {pg_us:.0f} µs.")
print("       -> With one client on the same machine both are dominated by the network round trip; no real difference.")

# Under load: 32 concurrent app users each opening their farm overview (latest reading of every pond)
import threading
def load_test(worker, n_threads=32, per_thread=300):
    done = []
    def run():
        c = worker()
        for i in range(per_thread): c(64 + (i % 100))
        done.append(per_thread)
    ts = [threading.Thread(target=run) for _ in range(n_threads)]
    t = time.perf_counter(); [x.start() for x in ts]; [x.join() for x in ts]
    return sum(done) / (time.perf_counter() - t)

def redis_worker():
    rc = redis.Redis(**REDIS)
    def farm_overview(fid):
        ponds = rc.smembers(f"farm:{fid}:ponds"); p = rc.pipeline(transaction=False)
        for u in ponds: p.hgetall(f"pond:{u}:latest")
        return p.execute()
    return farm_overview

def pg_worker():
    c = psycopg2.connect(dbname=os.getenv("PGDATABASE", "aquasense")); c.autocommit = True; k = c.cursor()
    q = """SELECT cu.unit_code, r.* FROM aqua.culture_unit cu CROSS JOIN LATERAL (
             SELECT recorded_at, dissolved_oxygen_mgl, ph, temperature_c FROM aqua.water_quality_reading w
             WHERE w.unit_id = cu.unit_id ORDER BY recorded_at DESC LIMIT 1) r WHERE cu.farm_id = %s"""
    def farm_overview(fid):
        k.execute(q, (fid,)); return k.fetchall()
    return farm_overview

redis_tput = load_test(redis_worker); pg_tput = load_test(pg_worker)
print(f"32 concurrent Python clients x 300 farm-overview screens: Redis = {redis_tput:,.0f} screens/s | "
      f"PostgreSQL = {pg_tput:,.0f} screens/s.")
print("       -> PostgreSQL was faster here. This test is client-bound: redis-py is pure Python (GIL-limited) and the")
print("          Redis version needs two round trips per screen (SMEMBERS, then a pipeline of HGETALLs), while psycopg2")
print("          is C and PostgreSQL returns the whole screen in one query. For a fair server comparison see")
print("          evidence/server_benchmark_output.txt (redis-benchmark vs pgbench, both C clients).")

# ---------------------------------------------------------------------------
h("S3  SORTED SET as a time window — dissolved oxygen for pond 316, last 24 hours")
end = epoch(dt.datetime.fromisoformat("2026-11-03T23:59:00+01:00")); start = end - 86400
rows = r.zrangebyscore("pond:316:do", start, end)
cmd(f"ZRANGEBYSCORE pond:316:do {start} {end}", f"{len(rows)} readings")
for m in rows:
    ts, v = m.split(":"); print(f"       {dt.datetime.fromtimestamp(int(ts), dt.timezone(dt.timedelta(hours=1))):%d %b %H:%M}  DO = {v} mg/L")
removed = r.zremrangebyscore("pond:316:do", "-inf", end - 3 * 86400)
cmd(f"ZREMRANGEBYSCORE pond:316:do -inf {end - 3*86400}   (retention: the app chart only needs 72 h)", f"{removed} old readings removed, {r.zcard('pond:316:do')} kept")

# ---------------------------------------------------------------------------
h("S4  STREAM + CONSUMER GROUP — live sensor ingestion for pond 3 (farm 1, Delta)")
r.xgroup_create("stream:readings", "alert-workers", id="0", mkstream=True)
r.xgroup_create("stream:readings", "db-writers", id="0", mkstream=True)
base = dt.datetime.fromisoformat("2026-11-04T00:00:00+01:00")
dos = [5.1, 4.6, 4.0, 3.4, 2.9, 2.3, 1.9, 1.7, 2.2, 3.1, 4.4, 5.6]          # a night-time oxygen crash
for i, do in enumerate(dos):
    ts = base + dt.timedelta(minutes=30 * i)
    r.xadd("stream:readings", {"unit_id": 3, "farm_id": 1, "ts": ts.isoformat(), "do": do, "ph": 7.3,
                               "temp": 27.8, "nh3": 0.12}, maxlen=100000, approximate=True)
cmd("XLEN stream:readings", r.xlen("stream:readings"))
cmd("XRANGE stream:readings - + COUNT 2", r.xrange("stream:readings", count=2))

# alert worker
msgs = r.xreadgroup("alert-workers", "worker-1", {"stream:readings": ">"}, count=100)[0][1]
alerts = 0
for mid, f in msgs:
    pipe = r.pipeline(transaction=True)                                  # MULTI ... EXEC
    pipe.hset(f"pond:{f['unit_id']}:latest", mapping={"ts": f["ts"], "do": f["do"], "ph": f["ph"],
                                                       "temp": f["temp"], "nh3": f["nh3"]})
    if float(f["do"]) < 2.5:
        pipe.zadd(f"farm:{f['farm_id']}:alerts", {f"LOW_DO|pond {f['unit_id']}|{f['ts']}|{f['do']} mg/L":
                                                   dt.datetime.fromisoformat(f["ts"]).timestamp()})
        pipe.publish(f"alerts:farm:{f['farm_id']}", f"Low oxygen {f['do']} mg/L in pond {f['unit_id']} - start aerator")
        alerts += 1
    pipe.xack("stream:readings", "alert-workers", mid)
    pipe.execute()
print(f"worker-1 processed {len(msgs)} messages, raised {alerts} low-oxygen alerts")
cmd("ZRANGE farm:1:alerts 0 -1", r.zrange("farm:1:alerts", 0, -1))
cmd("XPENDING stream:readings alert-workers", r.xpending("stream:readings", "alert-workers"))

# write-behind worker: batch the same messages into PostgreSQL (system of record)
msgs = r.xreadgroup("db-writers", "writer-1", {"stream:readings": ">"}, count=100)[0][1]
cur.execute("DELETE FROM water_quality_reading WHERE unit_id = 3 AND recorded_at >= '2026-11-04'")  # keep demo re-runnable
cur.executemany("""INSERT INTO water_quality_reading (unit_id, recorded_at, ph, dissolved_oxygen_mgl, temperature_c, ammonia_mgl, source)
                   VALUES (%s,%s,%s,%s,%s,%s,'sensor')""",
                [(f["unit_id"], f["ts"], f["ph"], f["do"], f["temp"], f["nh3"]) for _, f in msgs])
r.xack("stream:readings", "db-writers", *[m for m, _ in msgs])
cur.execute("SELECT count(*), min(dissolved_oxygen_mgl) FROM water_quality_reading WHERE unit_id = 3 AND recorded_at >= '2026-11-04'")
print(f"writer-1 batch-inserted {len(msgs)} rows into PostgreSQL -> verified in SQL: {cur.fetchone()} (count, min DO)")

# ---------------------------------------------------------------------------
h("S5  IDEMPOTENT OFFLINE SYNC — the phone retries an upload after a network drop")
key = "sync:device-7f3a:rec-000184"
cmd(f"SET {key} done NX EX 86400", r.set(key, "done", nx=True, ex=86400))
cmd(f"SET {key} done NX EX 86400   (retry of the same record)", r.set(key, "done", nx=True, ex=86400))
print("       First upload is processed; the retry returns None, so the server skips it -> no duplicate feeding log.")

# ---------------------------------------------------------------------------
h("S6  OTP LOGIN CODES with TTL (farmers log in with their phone number)")
cmd("SET otp:+2348030007919 482913 EX 300", r.set("otp:+2348030007919", "482913", ex=300))
cmd("TTL otp:+2348030007919", r.ttl("otp:+2348030007919"))
r.set("otp:+2348030015838", "771204", ex=2)
time.sleep(2.2)
cmd("GET otp:+2348030015838   (after 2.2 s, code was set with EX 2)", r.get("otp:+2348030015838"))

# ---------------------------------------------------------------------------
h("S7  LEADERBOARD — best feed conversion ratio (FCR) per cooperative, 2026 harvests")
cur.execute("""SELECT u.coop_id, f.farm_id, f.farm_name,
                      ROUND(SUM(fl.feed_kg) / NULLIF(SUM(h.total_weight_kg - b.initial_count*b.initial_avg_weight_g/1000),0), 3)
               FROM stock_batch b
               JOIN culture_unit cu ON cu.unit_id = b.unit_id
               JOIN farm f ON f.farm_id = cu.farm_id
               JOIN app_user u ON u.user_id = f.owner_id
               JOIN harvest h ON h.batch_id = b.batch_id
               JOIN LATERAL (SELECT SUM(qty_kg) feed_kg FROM feeding_log WHERE batch_id = b.batch_id) fl ON TRUE
               WHERE h.harvested_on >= '2026-01-01' AND u.coop_id IS NOT NULL
               GROUP BY u.coop_id, f.farm_id, f.farm_name""")
for coop, fid, name, fcr in cur.fetchall():
    r.zadd(f"coop:{coop}:fcr", {f"farm:{fid}": float(fcr)})
cur.execute("SELECT coop_name FROM cooperative WHERE coop_id = 5"); cname = cur.fetchone()[0]
top = r.zrange("coop:5:fcr", 0, 4, withscores=True)
cmd(f"ZRANGE coop:5:fcr 0 4 WITHSCORES   ({cname}; lower FCR = less feed per kg of fish)",
    [(m, r.hget(m, "name"), round(s, 3)) for m, s in top])
some = r.zrange("coop:5:fcr", -1, -1)[0]
cmd(f"ZRANK coop:5:fcr {some}", f"{r.zrank('coop:5:fcr', some) + 1} of {r.zcard('coop:5:fcr')}")

# ---------------------------------------------------------------------------
h("S8  GEO SEARCH — trader in Epe (6.583 N, 3.983 E) wants farms within 60 km with fish near harvest")
cur.execute("""SELECT DISTINCT cu.farm_id FROM stock_batch b JOIN culture_unit cu USING (unit_id)
               JOIN species s USING (species_id)
               WHERE b.status = 'active' AND DATE '2026-11-04' - b.stocked_on >= s.typical_cycle_days - 14""")
ready = [f"farm:{x[0]}" for x in cur.fetchall()]
if ready: r.sadd("harvest:ready", *ready)
near = r.geosearch("farms:geo", longitude=3.983, latitude=6.583, radius=60, unit="km", sort="ASC", withdist=True)
cmd("GEOSEARCH farms:geo FROMLONLAT 3.983 6.583 BYRADIUS 60 km ASC WITHDIST", f"{len(near)} farms")
for m, d in near[:8]:
    print(f"       {m:<9} {r.hget(m, 'name'):<28} {d:6.1f} km   harvest-ready: {r.sismember('harvest:ready', m)}")

# ---------------------------------------------------------------------------
h("S9  ATOMIC LUA SCRIPT — cached feed stock is decremented only if enough remains")
LUA = """
local stock = tonumber(redis.call('HGET', KEYS[1], ARGV[1]) or '0')
local want  = tonumber(ARGV[2])
if stock < want then return {0, tostring(stock)} end
local left = redis.call('HINCRBYFLOAT', KEYS[1], ARGV[1], -want)
return {1, left}
"""
use_feed = r.register_script(LUA)
r.hset("farm:1:feedstock", mapping={"feed:4": 100})
cmd("EVALSHA <use_feed> 1 farm:1:feedstock feed:4 30", use_feed(keys=["farm:1:feedstock"], args=["feed:4", 30]))
cmd("EVALSHA <use_feed> 1 farm:1:feedstock feed:4 90", use_feed(keys=["farm:1:feedstock"], args=["feed:4", 90]))
print("       Redis runs a Lua script as one atomic step (single-threaded), so the check-then-decrement cannot be")
print("       interleaved by another client — the same lost-update protection as SELECT ... FOR UPDATE in Portfolio 3.")

# ---------------------------------------------------------------------------
h("S10 CACHE-ASIDE — cooperative revenue dashboard for Lagos (expensive SQL, needed often)")
DASH = """SELECT date_trunc('month', s.sold_on)::date::text, ROUND(SUM(s.qty_kg)), ROUND(SUM(s.qty_kg*s.price_per_kg))
          FROM sale s JOIN harvest h USING (harvest_id) JOIN stock_batch b USING (batch_id)
          JOIN culture_unit cu USING (unit_id) JOIN farm f USING (farm_id)
          WHERE f.state_code = 'LA' AND s.sold_on >= '2026-01-01' GROUP BY 1 ORDER BY 1"""
def dashboard():
    hit = r.get("cache:dash:LA:2026")
    if hit: return "HIT", json.loads(hit)
    cur.execute(DASH); data = [[m, float(k), float(v)] for m, k, v in cur.fetchall()]
    r.set("cache:dash:LA:2026", json.dumps(data), ex=600)          # 10-minute freshness
    return "MISS", data
for i in range(2):
    t = time.perf_counter(); status, data = dashboard(); ms = (time.perf_counter() - t) * 1000
    print(f"       call {i+1}: cache {status:<4} {ms:7.2f} ms  ({len(data)} months)")
cmd("TTL cache:dash:LA:2026", r.ttl("cache:dash:LA:2026"))

# ---------------------------------------------------------------------------
h("S11 MEMORY AND PERSISTENCE")
for k in ["pond:316:latest", "pond:316:do", "stream:readings", "farms:geo", "coop:5:fcr"]:
    print(f"       MEMORY USAGE {k:<18} {r.memory_usage(k):>8,} bytes   TYPE {r.type(k)}")
info = r.info("memory"); pers = r.info("persistence")
print(f"       used_memory_human = {info['used_memory_human']}, DBSIZE = {r.dbsize()} keys")
print(f"       aof_enabled = {pers['aof_enabled']}, appendfsync = {r.config_get('appendfsync')['appendfsync']}, "
      f"maxmemory-policy = {r.config_get('maxmemory-policy')['maxmemory-policy']}")
print("\nDone.")
