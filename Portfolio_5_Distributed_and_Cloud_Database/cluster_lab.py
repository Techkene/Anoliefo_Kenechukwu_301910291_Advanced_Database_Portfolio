"""
AquaSense+ | Portfolio 5 | Distributed database lab (Windows and Linux)

Builds a separate 4-server PostgreSQL 16 lab in your user folder, copied from your
normal PostgreSQL (which is only read from and is never stopped):

    5440  lab primary  - a full copy of your server (pg_basebackup), system of record + shard coordinator
    5441  replica1     - streaming replica of the lab primary (hot standby, read-only)
    5442  shard1       - holds sensor readings for farms with hash(farm_id) % 2 = 0
    5443  shard2       - holds sensor readings for farms with hash(farm_id) % 2 = 1

The lab data lives in ~/aquasense_cluster (NOT in the Git repository).

Used from portfolio5_run.ipynb:
    import cluster_lab as lab
    lab.setup()                 # build (first time) or start the lab
    lab.replication_demo()      # R1-R6
    lab.sharding_setup(); lab.sharding_demo()   # S0-S6
    lab.failover_demo()         # F1-F5
    lab.stop_all()              # stop the four lab servers

Settings come from the usual libpq environment variables (PGPASSWORD, PGPORT for the
source server). Optional: PG_BIN (folder with pg_ctl), LAB_DIR.
"""
import contextlib, io, os, platform, re, secrets, shutil, statistics, subprocess, sys, threading, time
import psycopg2

WINDOWS = platform.system() == "Windows"
BIN = os.getenv("PG_BIN") or (r"C:\Program Files\PostgreSQL\16\bin" if WINDOWS else "/usr/lib/postgresql/16/bin")
LAB = os.getenv("LAB_DIR") or os.path.join(os.path.expanduser("~"), "aquasense_cluster")
SRC_PORT = int(os.getenv("PGPORT", "5432"))
PORTS = {"primary": 5440, "replica1": 5441, "shard1": 5442, "shard2": 5443}
EVIDENCE = "evidence"


# ----------------------------------------------------------------------------- helpers
def exe(name):
    return os.path.join(BIN, name + (".exe" if WINDOWS else ""))

def data_dir(name):
    return os.path.join(LAB, name)

def connect(port, db="aquasense", autocommit=True, timeout=5):
    c = psycopg2.connect(host="localhost", port=port, user="postgres", dbname=db, connect_timeout=timeout)
    c.autocommit = autocommit
    return c

def h(title):
    print("\n" + "=" * 100 + f"\n{title}\n" + "=" * 100, flush=True)

def show(cur, sql, args=None):
    cur.execute(sql, args)
    cols = [d[0] for d in cur.description]
    print("  " + " | ".join(cols))
    for r in cur.fetchall():
        print("  " + " | ".join(str(v) for v in r))

@contextlib.contextmanager
def cursor(port, db="aquasense"):
    """Autocommit cursor that closes its connection afterwards (psycopg2's own
    'with connection' opens a transaction block, which ALTER SYSTEM refuses)."""
    c = connect(port, db)
    try:
        yield c.cursor()
    finally:
        c.close()

def one(port, sql, db="aquasense"):
    c = connect(port, db)
    try:
        k = c.cursor(); k.execute(sql); return k.fetchone()[0]
    finally:
        c.close()

class _Tee(io.TextIOBase):
    def __init__(self, *streams): self.streams = streams
    def write(self, s):
        for st in self.streams: st.write(s)
        return len(s)
    def flush(self):
        for st in self.streams: st.flush()

@contextlib.contextmanager
def evidence(filename):
    """Print to the notebook AND save the same text to evidence/<filename>."""
    os.makedirs(EVIDENCE, exist_ok=True)
    with open(os.path.join(EVIDENCE, filename), "w", encoding="utf-8") as f:
        with contextlib.redirect_stdout(_Tee(sys.stdout, f)):
            yield
    print(f"\n[saved to {EVIDENCE}/{filename}]")


# ----------------------------------------------------------------------------- server control
def _opts(name):
    o = f"-p {PORTS[name]} -c listen_addresses=localhost"
    if name == "replica1":
        o += " -c cluster_name=replica1"
    if not WINDOWS:
        o += f" -k {LAB}"
    return o

def is_running(name):
    return subprocess.run([exe("pg_ctl"), "-D", data_dir(name), "status"],
                          stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL).returncode == 0

def wait_ready(port, seconds=60):
    end = time.time() + seconds
    while time.time() < end:
        try:
            connect(port, "postgres", timeout=2).close(); return True
        except psycopg2.OperationalError:
            time.sleep(0.5)
    raise RuntimeError(f"server on port {port} did not become ready - see {LAB}")

def start(name):
    if is_running(name):
        return
    # stdout goes to DEVNULL: on Windows the started server would otherwise keep a captured pipe open
    rc = subprocess.run([exe("pg_ctl"), "-D", data_dir(name), "-o", _opts(name),
                         "-l", os.path.join(LAB, f"{name}.log"), "-w", "-t", "120", "start"],
                        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL).returncode
    if rc != 0:
        log = os.path.join(LAB, f"{name}.log")
        tail = open(log, encoding="utf-8", errors="replace").read()[-1500:] if os.path.exists(log) else "(no log file)"
        raise RuntimeError(f"could not start {name}:\n{tail}")
    wait_ready(PORTS[name])

def stop(name, mode="fast"):
    if is_running(name):
        subprocess.run([exe("pg_ctl"), "-D", data_dir(name), "-m", mode, "-w", "stop"],
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

def run(cmd):
    res = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace")
    if res.returncode != 0:
        raise RuntimeError(" ".join(cmd[:2]) + " failed:\n" + res.stdout + res.stderr)
    return res.stdout + res.stderr

def _repl_password():
    p = os.path.join(LAB, "replicator_password.txt")
    return open(p).read().strip() if os.path.exists(p) else None

def _basebackup(src_port, target, replica=False):
    conninfo = f"host=localhost port={src_port} user=replicator password={_repl_password()}"
    cmd = [exe("pg_basebackup"), "-d", conninfo, "-D", data_dir(target), "-X", "stream", "-c", "fast"]
    if replica:
        cmd += ["-R", "-S", "replica1_slot"]
    run(cmd)

def roles():
    for name, port in PORTS.items():
        try:
            rec = one(port, "SELECT pg_is_in_recovery()", "postgres")
            print(f"  {name:<9} port {port}: " + ("standby (read-only)" if rec else "read-write"))
        except Exception:
            print(f"  {name:<9} port {port}: NOT RUNNING")


# ----------------------------------------------------------------------------- setup
def setup(fresh=False):
    """First run: copy your PostgreSQL into the lab and build replica + shards (5-15 min).
    Later runs: just start the four lab servers."""
    with evidence("cluster_setup_output.txt"):
        h("SETUP  4-node PostgreSQL lab")
        print(f"  binaries: {BIN}\n  lab folder: {LAB}\n  source server: localhost:{SRC_PORT}")
        built = os.path.exists(os.path.join(LAB, "setup_complete.txt"))   # written only when a build finishes
        if built and not fresh:
            for n in PORTS: start(n)
            print("  lab already built - servers started")
            roles(); return

        for n in PORTS: stop(n, "immediate")
        if os.path.exists(LAB):
            shutil.rmtree(LAB)
        os.makedirs(LAB)

        src = connect(SRC_PORT, "postgres"); k = src.cursor()
        k.execute("SELECT pg_database_size('aquasense')"); size = k.fetchone()[0]
        free = shutil.disk_usage(LAB).free
        print(f"  aquasense database: {size/1e6:,.0f} MB; free disk: {free/1e9:,.1f} GB")
        if free < size * 5:
            raise RuntimeError("Not enough free disk space for two copies of the database plus shards.")

        h("1. Replication role on your PostgreSQL (the only change made to it)")
        pw = secrets.token_hex(12)
        open(os.path.join(LAB, "replicator_password.txt"), "w").write(pw)
        k.execute("""DO $$ BEGIN
                       IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'replicator') THEN
                         CREATE ROLE replicator WITH REPLICATION LOGIN;
                       END IF; END $$""")
        k.execute("ALTER ROLE replicator WITH REPLICATION LOGIN PASSWORD %s", (pw,))
        src.close()
        print("  role 'replicator' ready (random password stored in the lab folder, not in Git)")

        h("2. Lab primary (port 5440): pg_basebackup copy of your server")
        t = time.perf_counter(); _basebackup(SRC_PORT, "primary")
        print(f"  copied in {time.perf_counter() - t:.1f} s")
        start("primary")
        with cursor(PORTS["primary"], "postgres") as k:
            for s in ("synchronous_standby_names", "synchronous_commit"):
                k.execute(f"ALTER SYSTEM RESET {s}")
            k.execute("SELECT pg_reload_conf()")
            k.execute("""SELECT pg_create_physical_replication_slot('replica1_slot')
                         WHERE NOT EXISTS (SELECT 1 FROM pg_replication_slots WHERE slot_name = 'replica1_slot')""")
        print("  started; physical replication slot 'replica1_slot' created")

        h("3. replica1 (port 5441): pg_basebackup -R -S replica1_slot from the lab primary")
        t = time.perf_counter(); _basebackup(PORTS["primary"], "replica1", replica=True)
        print(f"  copied in {time.perf_counter() - t:.1f} s (-R wrote standby.signal + primary_conninfo)")
        start("replica1")

        h("4. Two empty shard servers (ports 5442, 5443)")
        for n in ("shard1", "shard2"):
            run([exe("initdb"), "-D", data_dir(n), "-U", "postgres", "-A", "trust", "-E", "UTF8", "--locale=C"])
            start(n)
            with cursor(PORTS[n], "postgres") as k:
                k.execute("CREATE DATABASE aquasense_shard")
            print(f"  {n} initialised and started")
        time.sleep(2)
        open(os.path.join(LAB, "setup_complete.txt"), "w").write(time.ctime())
        h("Cluster ready")
        roles()

def start_all():
    for n in PORTS: start(n)
    roles()

def stop_all():
    for n in PORTS: stop(n)
    print("All four lab servers stopped (your normal PostgreSQL is untouched).")


# ----------------------------------------------------------------------------- Part A: replication
def replication_demo():
    with evidence("replication_demo_output.txt"):
        P, R = connect(PORTS["primary"]), connect(PORTS["replica1"])
        pc, rc = P.cursor(), R.cursor()
        pc.execute("CREATE TABLE IF NOT EXISTS aqua.replication_heartbeat (id BIGSERIAL PRIMARY KEY, note TEXT, written_at TIMESTAMPTZ DEFAULT clock_timestamp())")
        for s in ("synchronous_standby_names", "synchronous_commit"):
            pc.execute(f"ALTER SYSTEM RESET {s}")
        pc.execute("SELECT pg_reload_conf()"); time.sleep(1)

        h("R1  REPLICATION STATUS")
        print("On the PRIMARY (pg_stat_replication):")
        show(pc, """SELECT application_name, client_addr, state, sync_state, sent_lsn, replay_lsn,
                           pg_wal_lsn_diff(sent_lsn, replay_lsn) AS bytes_behind, replay_lag FROM pg_stat_replication""")
        print("\nOn the REPLICA (pg_stat_wal_receiver):")
        show(rc, "SELECT status, slot_name, sender_port, latest_end_lsn, pg_is_in_recovery() AS in_recovery FROM pg_stat_wal_receiver")
        print("\nReplication slot (keeps WAL until the replica has it, even if it disconnects):")
        show(pc, "SELECT slot_name, slot_type, active, restart_lsn FROM pg_replication_slots")

        h("R2  REPLICATION LAG - insert on primary, poll replica until visible (30 trials, async mode)")
        lags = []
        for i in range(30):
            pc.execute("INSERT INTO aqua.replication_heartbeat (note) VALUES (%s) RETURNING id", (f"lag test {i}",))
            rid = pc.fetchone()[0]; t = time.perf_counter()
            while True:
                rc.execute("SELECT 1 FROM aqua.replication_heartbeat WHERE id = %s", (rid,))
                if rc.fetchone(): break
            lags.append((time.perf_counter() - t) * 1000)
        print(f"  visible on replica after: median {statistics.median(lags):.2f} ms, max {max(lags):.2f} ms, min {min(lags):.2f} ms")
        print("  -> eventual consistency window: a read from the replica right after a save can briefly miss it.")

        h("R3  THE REPLICA IS READ-ONLY")
        try:
            rc.execute("INSERT INTO aqua.replication_heartbeat (note) VALUES ('write to replica')")
        except psycopg2.Error as e:
            print(f"  SQLSTATE {e.pgcode}: {e.pgerror.strip()}")

        h("R4  READ SCALING - cooperative revenue report served by the replica, not the primary")
        t = time.perf_counter()
        show(rc, """SELECT f.state_code, ROUND(SUM(s.qty_kg)) AS kg_2026, ROUND(SUM(s.qty_kg*s.price_per_kg)/1e6, 1) AS revenue_ngn_million
                    FROM aqua.sale s JOIN aqua.harvest h USING (harvest_id) JOIN aqua.stock_batch b USING (batch_id)
                    JOIN aqua.culture_unit cu USING (unit_id) JOIN aqua.farm f USING (farm_id)
                    WHERE s.sold_on >= '2026-01-01' GROUP BY 1 ORDER BY 3 DESC LIMIT 5""")
        print(f"  ({(time.perf_counter()-t)*1000:.1f} ms on the replica; the primary did none of this work)")

        def commit_latency(n=300):
            c = connect(PORTS["primary"]); k = c.cursor(); ts = []
            for _ in range(n):
                t = time.perf_counter(); k.execute("INSERT INTO aqua.replication_heartbeat (note) VALUES ('latency')")
                ts.append((time.perf_counter() - t) * 1000)
            c.close(); return statistics.median(ts), sorted(ts)[int(n * 0.95)]

        h("R5  ASYNC vs SYNC COMMIT LATENCY (300 single-row commits each)")
        a_med, a_p95 = commit_latency()
        pc.execute("ALTER SYSTEM SET synchronous_standby_names = 'replica1'")
        pc.execute("ALTER SYSTEM SET synchronous_commit = 'remote_apply'")
        pc.execute("SELECT pg_reload_conf()"); time.sleep(1.5)
        show(pc, "SELECT application_name, state, sync_state FROM pg_stat_replication")
        s_med, s_p95 = commit_latency()
        print(f"\n  asynchronous             : median {a_med:.3f} ms, p95 {a_p95:.3f} ms")
        print(f"  synchronous remote_apply : median {s_med:.3f} ms, p95 {s_p95:.3f} ms  ({s_med/a_med:.1f}x slower)")
        pc.execute("INSERT INTO aqua.replication_heartbeat (note) VALUES ('read-your-writes') RETURNING id"); rid = pc.fetchone()[0]
        rc.execute("SELECT count(*) FROM aqua.replication_heartbeat WHERE id = %s", (rid,))
        print(f"  read-your-writes check: row {rid} visible on replica immediately after COMMIT -> {rc.fetchone()[0] == 1}")
        print("  (both servers are on one laptop; across data centres every commit would also pay the network round trip)")

        h("R6  SYNC REPLICATION WITH THE REPLICA DOWN - consistency over availability")
        R.close(); stop("replica1")
        print("  replica1 stopped.")
        result = {}
        def blocked_write():
            c = connect(PORTS["primary"]); k = c.cursor(); t = time.perf_counter()
            k.execute("INSERT INTO aqua.replication_heartbeat (note) VALUES ('during outage')")
            result["secs"] = time.perf_counter() - t; c.close()
        th = threading.Thread(target=blocked_write); th.start(); th.join(timeout=5)
        if th.is_alive():
            print("  INSERT on primary still waiting after 5 s -> writes are BLOCKED")
            show(pc, "SELECT pid, state, wait_event_type, wait_event, left(query, 60) AS query FROM pg_stat_activity WHERE wait_event = 'SyncRep'")
        print("  Operator decision: drop to asynchronous replication to restore availability.")
        for s in ("synchronous_standby_names", "synchronous_commit"):
            pc.execute(f"ALTER SYSTEM RESET {s}")
        pc.execute("SELECT pg_reload_conf()")
        th.join()
        print(f"  -> the blocked INSERT committed {result['secs']:.1f} s after it started.")
        start("replica1"); time.sleep(3)
        print("  replica1 restarted and caught up from the replication slot:")
        show(pc, "SELECT application_name, state, sync_state, pg_wal_lsn_diff(sent_lsn, replay_lsn) AS bytes_behind FROM pg_stat_replication")
        R = connect(PORTS["replica1"]); rc = R.cursor()
        rc.execute("SELECT count(*) FROM aqua.replication_heartbeat WHERE note = 'during outage'")
        print(f"  row written during the outage now on replica: {rc.fetchone()[0] >= 1}")
        P.close(); R.close()


# ----------------------------------------------------------------------------- Part B: sharding
SHARD_TABLE = """CREATE TABLE IF NOT EXISTS readings (farm_id INT NOT NULL, unit_id INT NOT NULL,
    recorded_at TIMESTAMPTZ NOT NULL, ph NUMERIC(4,2), dissolved_oxygen_mgl NUMERIC(5,2),
    temperature_c NUMERIC(4,1), ammonia_mgl NUMERIC(5,3), source VARCHAR(6) NOT NULL);
    CREATE INDEX IF NOT EXISTS readings_farm_unit_time ON readings (farm_id, unit_id, recorded_at DESC);"""

def sharding_setup():
    with evidence("sharding_setup_output.txt"):
        h("S0  SHARDING SETUP - hash(farm_id) over 2 shard servers via postgres_fdw")
        for n in ("shard1", "shard2"):
            with cursor(PORTS[n], "aquasense_shard") as k:
                k.execute("DROP TABLE IF EXISTS readings"); k.execute(SHARD_TABLE)
            print(f"  {n}: table 'readings' + index created")
        c = connect(PORTS["primary"]); k = c.cursor()
        for sql in [
            "CREATE EXTENSION IF NOT EXISTS postgres_fdw",
            "DROP TABLE IF EXISTS aqua.wqr_dist CASCADE",
            "DROP SERVER IF EXISTS shard1 CASCADE", "DROP SERVER IF EXISTS shard2 CASCADE",
            "CREATE SERVER shard1 FOREIGN DATA WRAPPER postgres_fdw OPTIONS (host 'localhost', port '5442', dbname 'aquasense_shard', async_capable 'true', batch_size '5000', fetch_size '10000')",
            "CREATE SERVER shard2 FOREIGN DATA WRAPPER postgres_fdw OPTIONS (host 'localhost', port '5443', dbname 'aquasense_shard', async_capable 'true', batch_size '5000', fetch_size '10000')",
            "CREATE USER MAPPING FOR postgres SERVER shard1 OPTIONS (user 'postgres')",
            "CREATE USER MAPPING FOR postgres SERVER shard2 OPTIONS (user 'postgres')",
            """CREATE TABLE aqua.wqr_dist (farm_id INT NOT NULL, unit_id INT NOT NULL, recorded_at TIMESTAMPTZ NOT NULL,
                 ph NUMERIC(4,2), dissolved_oxygen_mgl NUMERIC(5,2), temperature_c NUMERIC(4,1),
                 ammonia_mgl NUMERIC(5,3), source VARCHAR(6) NOT NULL) PARTITION BY HASH (farm_id)""",
            """CREATE FOREIGN TABLE aqua.wqr_dist_p0 PARTITION OF aqua.wqr_dist FOR VALUES WITH (MODULUS 2, REMAINDER 0)
                 SERVER shard1 OPTIONS (schema_name 'public', table_name 'readings')""",
            """CREATE FOREIGN TABLE aqua.wqr_dist_p1 PARTITION OF aqua.wqr_dist FOR VALUES WITH (MODULUS 2, REMAINDER 1)
                 SERVER shard2 OPTIONS (schema_name 'public', table_name 'readings')"""]:
            k.execute(sql)
        print("  coordinator (lab primary): foreign servers, user mappings, hash-partitioned table aqua.wqr_dist")
        t = time.perf_counter()
        k.execute("""INSERT INTO aqua.wqr_dist
                     SELECT cu.farm_id, w.unit_id, w.recorded_at, w.ph, w.dissolved_oxygen_mgl, w.temperature_c, w.ammonia_mgl, w.source
                     FROM aqua.water_quality_reading w JOIN aqua.culture_unit cu USING (unit_id)""")
        print(f"  routed {k.rowcount:,} readings to the shards in {time.perf_counter() - t:.1f} s")
        for n in ("shard1", "shard2"):
            with cursor(PORTS[n], "aquasense_shard") as sk:
                sk.execute("ANALYZE readings")
        k.execute("ANALYZE aqua.wqr_dist_p0"); k.execute("ANALYZE aqua.wqr_dist_p1")
        c.close()

def _timed(cur, sql, setup=None, runs=3):
    ts = []
    for _ in range(runs):
        if setup: cur.execute(setup)
        t = time.perf_counter(); cur.execute(sql); cur.fetchall(); ts.append((time.perf_counter() - t) * 1000)
    return statistics.median(ts)

def sharding_demo():
    with evidence("sharding_demo_output.txt"):
        c = connect(PORTS["primary"]); k = c.cursor()
        h("S1  DATA DISTRIBUTION - counted directly on each shard server")
        for n in ("shard1", "shard2"):
            with cursor(PORTS[n], "aquasense_shard") as sk:
                print(f"{n} (port {PORTS[n]}):"); show(sk, "SELECT count(*) AS readings, count(DISTINCT farm_id) AS farms, pg_size_pretty(pg_total_relation_size('readings')) AS size FROM readings")
        print("Does any farm exist on both shards? (must be 0 - all of a farm's data is co-located)")
        show(k, "SELECT count(*) AS farms_on_both FROM (SELECT DISTINCT farm_id FROM aqua.wqr_dist_p0) a JOIN (SELECT DISTINCT farm_id FROM aqua.wqr_dist_p1) b USING (farm_id)")

        h("S2  WHY HASH AND NOT REGION? - skew if we had sharded South vs North")
        show(k, """SELECT CASE WHEN s.geo_zone LIKE 'South%%' THEN 'South shard' ELSE 'North shard' END AS region_shard,
                          count(DISTINCT f.farm_id) AS farms, ROUND(100.0*count(*)/SUM(count(*)) OVER (), 1) AS pct_of_readings
                   FROM aqua.water_quality_reading w JOIN aqua.culture_unit cu USING (unit_id)
                   JOIN aqua.farm f USING (farm_id) JOIN aqua.state s USING (state_code) GROUP BY 1""")
        print()
        show(k, """SELECT tableoid::regclass AS hash_partition, count(DISTINCT farm_id) AS farms,
                          ROUND(100.0*count(*)/SUM(count(*)) OVER (), 1) AS pct_of_readings
                   FROM aqua.wqr_dist GROUP BY 1 ORDER BY 1""")

        h("S3  SINGLE-SHARD QUERY - farm 64's pond dashboard: partition pruning sends it to ONE server")
        k.execute("""EXPLAIN (ANALYZE, VERBOSE, COSTS OFF) SELECT unit_id, recorded_at, dissolved_oxygen_mgl FROM aqua.wqr_dist
                     WHERE farm_id = 64 AND unit_id = 316 ORDER BY recorded_at DESC LIMIT 12""")
        print("\n".join(r[0] for r in k.fetchall()))

        h("S4  CROSS-SHARD QUERY - readings and average oxygen per farm (scatter-gather)")
        agg = "SELECT farm_id, count(*), round(avg(dissolved_oxygen_mgl),2) FROM aqua.wqr_dist GROUP BY farm_id"
        off = _timed(k, agg, "SET enable_partitionwise_aggregate = off")
        on = _timed(k, agg, "SET enable_partitionwise_aggregate = on")
        local = _timed(k, """SELECT cu.farm_id, count(*), round(avg(w.dissolved_oxygen_mgl),2) FROM aqua.water_quality_reading w
                             JOIN aqua.culture_unit cu USING (unit_id) GROUP BY cu.farm_id""")
        print(f"  4a pushdown OFF (raw rows shipped to the coordinator): {off:8.1f} ms")
        print(f"  4b pushdown ON  (each shard runs its own GROUP BY)     : {on:8.1f} ms")
        print(f"  4c same aggregate on the single-server table          : {local:8.1f} ms")
        print("\nPlan with pushdown (note Async Foreign Scan on both shards and the Remote SQL):")
        k.execute("SET enable_partitionwise_aggregate = on")
        k.execute("EXPLAIN (VERBOSE, COSTS OFF) " + agg)
        print("\n".join(r[0] for r in k.fetchall()))

        h("S5  WRITE ROUTING - a new reading for farm 64 goes to the correct shard automatically")
        k.execute("INSERT INTO aqua.wqr_dist VALUES (64, 316, '2026-11-04 06:00+01', 7.0, 3.9, 27.1, 0.08, 'sensor')")
        for n in ("shard1", "shard2"):
            print(f"  on {n}: " + str(one(PORTS[n], "SELECT count(*) FROM readings WHERE farm_id=64 AND recorded_at='2026-11-04 06:00+01'", "aquasense_shard")))
        k.execute("DELETE FROM aqua.wqr_dist WHERE farm_id=64 AND recorded_at='2026-11-04 06:00+01'")

        h("S6  SHARD FAILURE - shard2 goes down")
        k.execute("SELECT farm_id FROM aqua.wqr_dist_p0 LIMIT 1"); up = k.fetchone()[0]
        k.execute("SELECT farm_id FROM aqua.wqr_dist_p1 LIMIT 1"); down = k.fetchone()[0]
        c.close(); stop("shard2"); print("  shard2 stopped.")
        c = connect(PORTS["primary"]); k = c.cursor()
        print(f"  Farm {up} (lives on shard1) - still works:")
        show(k, f"SELECT farm_id, count(*) AS readings FROM aqua.wqr_dist WHERE farm_id = {up} GROUP BY 1")
        for label, sql in ((f"Farm {down} (lives on shard2)", f"SELECT count(*) FROM aqua.wqr_dist WHERE farm_id = {down}"),
                           ("Cross-shard cooperative report", "SELECT count(*) FROM aqua.wqr_dist")):
            try:
                k.execute(sql); print(f"  {label}: {k.fetchone()}")
            except psycopg2.Error as e:
                print(f"  {label}: {e.pgerror.strip().splitlines()[0]}")
        c.close(); start("shard2"); print("  shard2 restarted.")
        print(f"  total readings after recovery: {one(PORTS['primary'], 'SELECT count(*) FROM aqua.wqr_dist'):,}")


# ----------------------------------------------------------------------------- Part C: failover
def failover_demo():
    with evidence("failover_demo_output.txt"):
        h("F1  BEFORE: roles, timelines and the last farm record written")
        for n in ("primary", "replica1"):
            print(f"  {n:<9} port {PORTS[n]} in_recovery={one(PORTS[n], 'SELECT pg_is_in_recovery()')} "
                  f"timeline={one(PORTS[n], 'SELECT timeline_id FROM pg_control_checkpoint()')}")
        last_id = one(PORTS["primary"], "INSERT INTO aqua.replication_heartbeat (note) VALUES ('last write before crash') RETURNING id")
        print(f"  'last write before crash' written with id={last_id}")
        time.sleep(0.3)

        h("F2  CRASH: lab primary killed with an immediate shutdown (no clean checkpoint)")
        t0 = time.perf_counter(); stop("primary", "immediate"); print("  primary down at t=0")
        try:
            connect(PORTS["primary"], timeout=2)
        except psycopg2.OperationalError as e:
            print("  connect to primary -> " + str(e).strip().splitlines()[0][:110])
        print("  replica still serves READS during the outage:")
        with cursor(PORTS["replica1"]) as rk:
            show(rk, "SELECT count(*) AS farms, (SELECT note FROM aqua.replication_heartbeat ORDER BY id DESC LIMIT 1) AS last_record FROM aqua.farm")

        h("F3  FAILOVER: promote replica1 to primary")
        print(f"  pg_promote() returned {one(PORTS['replica1'], 'SELECT pg_promote(wait => true)')}")
        t1 = time.perf_counter()
        print(f"  in_recovery={one(PORTS['replica1'], 'SELECT pg_is_in_recovery()')}")
        time.sleep(1)
        with cursor(PORTS["replica1"]) as rk:
            rk.execute("CHECKPOINT")
        print(f"  new timeline={one(PORTS['replica1'], 'SELECT timeline_id FROM pg_control_checkpoint()')}")
        print("  " + str(one(PORTS["replica1"], "INSERT INTO aqua.replication_heartbeat (note) VALUES ('first write after failover') RETURNING 'write accepted on new primary, id=' || id")))
        t2 = time.perf_counter()
        print(f"  RTO (crash -> promotion complete): {(t1 - t0)*1000:.0f} ms;  crash -> first successful write: {(t2 - t0)*1000:.0f} ms")
        print("  (manual, scripted promotion; real RTO also includes failure detection, e.g. Patroni's ~30 s leader TTL)")
        rpo = one(PORTS["replica1"], f"SELECT count(*) = 1 FROM aqua.replication_heartbeat WHERE id = {last_id}")
        print(f"  RPO: row {last_id} ('last write before crash') present on the new primary -> {rpo}"
              + ("  => 0 rows lost" if rpo else "  => data LOST"))

        h("F4  SPLIT-BRAIN RISK")
        print("  If the old primary simply restarted as a writer, the app could write to two primaries on different timelines.")
        print("  Production fix: fence the old node, then rejoin it as a replica with pg_rewind or a fresh pg_basebackup.")

        h("F5  RESTORE THE LAB (old primary back as writer, replica rebuilt from it)")
        stop("replica1"); start("primary")
        shutil.rmtree(data_dir("replica1"))
        _basebackup(PORTS["primary"], "replica1", replica=True); start("replica1"); time.sleep(2)
        with cursor(PORTS["primary"]) as k:
            show(k, "SELECT application_name, state, sync_state FROM pg_stat_replication")
