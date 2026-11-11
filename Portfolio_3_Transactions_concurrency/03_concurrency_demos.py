"""
AquaSense+ | Portfolio 3 | Concurrency problems and control mechanisms

Two (sometimes three) real PostgreSQL sessions run against the same rows.
Session A and B are two farm attendants / traders using the app at the same
moment. Every step is printed with a timestamp so the interleaving is visible.

  D1  Lost update under READ COMMITTED (app does read -> compute -> write)
  D2  Fix 1: pessimistic locking with SELECT ... FOR UPDATE (+ pg_locks evidence)
  D3  Fix 2: REPEATABLE READ detects the conflict -> serialization error -> retry
  D4  Non-repeatable read: READ COMMITTED vs REPEATABLE READ snapshot
  D5  Write skew / phantom oversell: REPEATABLE READ vs SERIALIZABLE vs row lock
  D6  Deadlock between two feed transfers, and the lock-ordering fix

Usage: python3 03_concurrency_demos.py | tee evidence/concurrency_demo_output.txt
"""
import os, threading, time
import psycopg2
from psycopg2 import errors
from psycopg2.extensions import (ISOLATION_LEVEL_READ_COMMITTED as RC,
                                 ISOLATION_LEVEL_REPEATABLE_READ as RR,
                                 ISOLATION_LEVEL_SERIALIZABLE as SER)

DB = os.getenv("PGDATABASE", "aquasense")
T0 = time.perf_counter()
FARM, FEED, OTHER_FARM = 1, 4, 5
print_lock = threading.Lock()

def log(who, msg):
    with print_lock:
        print(f"[{time.perf_counter() - T0:6.2f}s] {who:<7} {msg}", flush=True)

class Session:
    def __init__(self, name, level=RC):
        self.name = name
        self.conn = psycopg2.connect(dbname=DB, application_name=f"aquasense-{name}")
        self.conn.set_isolation_level(level)
        self.cur = self.conn.cursor()
        self.cur.execute("SET search_path TO aqua")
        self.conn.commit()
        self.cur.execute("SELECT pg_backend_pid()"); self.pid = self.cur.fetchone()[0]
        self.conn.commit()

    def q(self, sql, args=None, show=True):
        self.cur.execute(sql, args)
        res = self.cur.fetchall() if self.cur.description else None
        if show:
            log(self.name, f"{sql.strip()[:90]:<90} -> {res if res is not None else self.cur.statusmessage}")
        return res

    def commit(self):  self.conn.commit();   log(self.name, "COMMIT")
    def rollback(self): self.conn.rollback(); log(self.name, "ROLLBACK")
    def close(self):   self.conn.close()

def admin():
    c = psycopg2.connect(dbname=DB); c.autocommit = True
    cur = c.cursor(); cur.execute("SET search_path TO aqua"); return c, cur

def reset_stock(qty=100):
    c, cur = admin()
    cur.execute("""INSERT INTO feed_inventory VALUES (%s,%s,%s,now())
                   ON CONFLICT (farm_id, feed_id) DO UPDATE SET qty_kg = EXCLUDED.qty_kg""", (FARM, FEED, qty))
    c.close()

def stock():
    c, cur = admin()
    cur.execute("SELECT qty_kg FROM feed_inventory WHERE farm_id=%s AND feed_id=%s", (FARM, FEED))
    v = cur.fetchone()[0]; c.close(); return v

def header(t):
    print("\n" + "=" * 110 + f"\n{t}\n" + "=" * 110, flush=True)

READ = "SELECT qty_kg FROM feed_inventory WHERE farm_id = 1 AND feed_id = 4"

# ---------------------------------------------------------------------------
header("D1  LOST UPDATE — READ COMMITTED, app reads stock then writes the new value\n"
       "    Store has 100 kg. Attendant A records 30 kg, attendant B records 20 kg. Correct result: 50 kg.")
reset_stock(100)
A, B = Session("A"), Session("B")
a = A.q(READ)[0][0]
b = B.q(READ)[0][0]
A.q("UPDATE feed_inventory SET qty_kg = %s WHERE farm_id = 1 AND feed_id = 4", (a - 30,))
A.commit()
B.q("UPDATE feed_inventory SET qty_kg = %s WHERE farm_id = 1 AND feed_id = 4", (b - 20,))
B.commit()
log("CHECK", f"final stock = {stock()} kg  (expected 50) -> A's 30 kg deduction was LOST")
A.close(); B.close()

# ---------------------------------------------------------------------------
header("D2  FIX 1 — pessimistic locking: SELECT ... FOR UPDATE\n"
       "    B's read blocks until A commits, then B sees A's result.")
reset_stock(100)
A, B = Session("A"), Session("B")
a = A.q(READ + " FOR UPDATE")[0][0]

def b_work():
    t = time.perf_counter()
    val = B.q(READ + " FOR UPDATE")[0][0]
    log("B", f"(waited {time.perf_counter() - t:.2f}s for A's row lock)")
    B.q("UPDATE feed_inventory SET qty_kg = %s WHERE farm_id = 1 AND feed_id = 4", (val - 20,))
    B.commit()

th = threading.Thread(target=b_work); th.start()
time.sleep(1.0)
c, cur = admin()
cur.execute("""SELECT a.application_name, a.wait_event_type, a.wait_event, l.mode, l.granted,
                      pg_blocking_pids(a.pid) AS blocked_by
               FROM pg_stat_activity a JOIN pg_locks l ON l.pid = a.pid
               WHERE a.pid = %s AND l.locktype = 'transactionid' AND NOT l.granted""", (B.pid,))
log("MONITOR", f"pg_stat_activity/pg_locks for B: {cur.fetchall()}  (A pid = {A.pid})")
c.close()
A.q("UPDATE feed_inventory SET qty_kg = %s WHERE farm_id = 1 AND feed_id = 4", (a - 30,))
A.commit()
th.join()
log("CHECK", f"final stock = {stock()} kg  (expected 50) -> correct")
A.close(); B.close()

# ---------------------------------------------------------------------------
header("D3  FIX 2 — optimistic: REPEATABLE READ detects the conflicting update; app retries")
reset_stock(100)
A, B = Session("A", RR), Session("B", RR)
a = A.q(READ)[0][0]
b = B.q(READ)[0][0]
A.q("UPDATE feed_inventory SET qty_kg = %s WHERE farm_id = 1 AND feed_id = 4", (a - 30,))
A.commit()
for attempt in (1, 2):
    try:
        B.q("UPDATE feed_inventory SET qty_kg = %s WHERE farm_id = 1 AND feed_id = 4", (b - 20,))
        B.commit(); break
    except errors.SerializationFailure as e:
        log("B", f"ERROR 40001 {e.pgerror.strip().splitlines()[0]}")
        B.rollback()
        log("B", f"retry #{attempt} with a fresh snapshot")
        b = B.q(READ)[0][0]
log("CHECK", f"final stock = {stock()} kg  (expected 50) -> correct after one retry")
A.close(); B.close()

# ---------------------------------------------------------------------------
for level, name in ((RC, "READ COMMITTED"), (RR, "REPEATABLE READ")):
    header(f"D4  NON-REPEATABLE READ under {name}\n"
           "    A builds a stock report and reads the same row twice; B commits a delivery in between.")
    reset_stock(100)
    A, B = Session("A", level), Session("B")
    first = A.q(READ)[0][0]
    B.q("UPDATE feed_inventory SET qty_kg = qty_kg + 150 WHERE farm_id = 1 AND feed_id = 4")
    B.commit()
    second = A.q(READ)[0][0]
    A.commit()
    verdict = "CHANGED inside one transaction (non-repeatable read)" if first != second else "STABLE (same snapshot for the whole transaction)"
    log("CHECK", f"A read {first} then {second} -> {verdict}")
    A.close(); B.close()

# ---------------------------------------------------------------------------
c, cur = admin()
cur.execute("""INSERT INTO harvest (batch_id, harvested_on, fish_count, total_weight_kg, is_partial)
               SELECT batch_id, CURRENT_DATE, 1000, 1000, TRUE FROM stock_batch WHERE status='harvested'
               ORDER BY batch_id LIMIT 1 RETURNING harvest_id""")
HID = cur.fetchone()[0]; c.close()
CHECK = f"SELECT COALESCE(SUM(qty_kg),0) FROM sale WHERE harvest_id = {HID}"
SELL = f"INSERT INTO sale (harvest_id, trader_id, qty_kg, price_per_kg, sold_on) VALUES ({HID}, %s, 600, 3200, CURRENT_DATE)"

def clear_sales():
    c, cur = admin(); cur.execute(f"DELETE FROM sale WHERE harvest_id = {HID}"); c.close()

def sold():
    c, cur = admin(); cur.execute(CHECK); v = cur.fetchone()[0]; c.close(); return v

for level, name in ((RR, "REPEATABLE READ"), (SER, "SERIALIZABLE")):
    header(f"D5  WRITE SKEW under {name}\n"
           f"    Harvest {HID} has 1000 kg. Traders A and B each want 600 kg. Rule: total sold <= 1000 kg.\n"
           "    Each checks what is left, sees 1000 kg, and inserts a NEW sale row (no row is updated twice).")
    clear_sales()
    A, B = Session("A", level), Session("B", level)
    left_a = 1000 - A.q(CHECK)[0][0]
    left_b = 1000 - B.q(CHECK)[0][0]
    if left_a >= 600: A.q(SELL, (525,))
    if left_b >= 600: B.q(SELL, (526,))
    A.commit()
    try:
        B.commit()
    except errors.SerializationFailure as e:
        log("B", f"ERROR 40001 {e.pgerror.strip().splitlines()[0]}")
        B.rollback()
    total = sold()
    log("CHECK", f"total sold = {total} kg of 1000 kg -> " + ("OVERSOLD (anomaly allowed)" if total > 1000 else "rule held"))
    A.close(); B.close()

header("D5b FIX in READ COMMITTED: sell_fish() locks the harvest row (FOR UPDATE) before checking")
clear_sales()
A, B = Session("A"), Session("B")
A.q("SELECT sell_fish(%s, 525, 600, 3200)", (HID,))
def b_sell():
    try:
        B.q("SELECT sell_fish(%s, 526, 600, 3200)", (HID,))
        B.commit()
    except psycopg2.Error as e:                      # custom SQLSTATE AQ003 raised by sell_fish()
        log("B", f"ERROR {e.pgcode} {e.pgerror.strip().splitlines()[0]}")
        B.rollback()
th = threading.Thread(target=b_sell); th.start()
time.sleep(1.0); A.commit(); th.join()
log("CHECK", f"total sold = {sold()} kg of 1000 kg -> rule held")
A.close(); B.close()
c, cur = admin(); cur.execute(f"DELETE FROM sale WHERE harvest_id={HID}"); cur.execute(f"DELETE FROM harvest WHERE harvest_id={HID}"); c.close()

# ---------------------------------------------------------------------------
header("D6  DEADLOCK — two feed transfers in opposite directions, rows locked in opposite order\n"
       "    A: farm 1 -> farm 5 (locks farm 1 first).  B: farm 5 -> farm 1 (locks farm 5 first).")
c, cur = admin()
cur.execute("UPDATE feed_inventory SET qty_kg = 100 WHERE feed_id = 4 AND farm_id IN (1, 5)")
c.close()
A, B = Session("A"), Session("B")
A.q("UPDATE feed_inventory SET qty_kg = qty_kg - 10 WHERE farm_id = 1 AND feed_id = 4")
B.q("UPDATE feed_inventory SET qty_kg = qty_kg - 10 WHERE farm_id = 5 AND feed_id = 4")
results = {}
def step2(sess, farm):
    try:
        sess.q(f"UPDATE feed_inventory SET qty_kg = qty_kg + 10 WHERE farm_id = {farm} AND feed_id = 4")
        sess.commit(); results[sess.name] = "committed"
    except errors.DeadlockDetected as e:
        log(sess.name, "ERROR 40P01 " + " ".join(e.pgerror.split())[:150])
        sess.rollback(); results[sess.name] = "aborted (deadlock victim)"
ta = threading.Thread(target=step2, args=(A, 5)); tb = threading.Thread(target=step2, args=(B, 1))
ta.start(); time.sleep(0.2); tb.start(); ta.join(); tb.join()
log("CHECK", f"outcome: {results} -> PostgreSQL detected the cycle after deadlock_timeout (1 s) and aborted one")
A.close(); B.close()

header("D6b FIX — transfer_feed() always locks rows in ascending farm_id order")
c, cur = admin()
cur.execute("UPDATE feed_inventory SET qty_kg = 100 WHERE feed_id = 4 AND farm_id IN (1, 5)")
c.close()
A, B = Session("A"), Session("B")
A.q("CALL transfer_feed(1, 5, 4, 10)")
def b_tx():
    t = time.perf_counter()
    B.q("CALL transfer_feed(5, 1, 4, 30)")
    log("B", f"(waited {time.perf_counter() - t:.2f}s — queued behind A instead of deadlocking)")
    B.commit()
th = threading.Thread(target=b_tx); th.start(); time.sleep(0.8); A.commit(); th.join()
c, cur = admin()
cur.execute("SELECT farm_id, qty_kg FROM feed_inventory WHERE feed_id = 4 AND farm_id IN (1,5) ORDER BY farm_id")
rows = cur.fetchall(); c.close()
log("CHECK", f"balances {rows}; total = {sum(r[1] for r in rows)} kg (expected 200, farm1 = 120, farm5 = 80)")
A.close(); B.close()
reset_stock(100)
print("\nAll demos finished.")
