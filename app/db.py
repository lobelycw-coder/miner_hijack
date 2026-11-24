"""SQLite store on the data dir. WAL, single writer lock, restore on boot."""
import os, sqlite3, threading, time
from . import backup

DATA = os.environ.get("DATA_DIR", "./data")
os.makedirs(DATA, exist_ok=True)
_DB_PATH = os.path.join(DATA, "scroof.db")

backup.restore_if_missing()

_LOCK = threading.RLock()
_CONN = sqlite3.connect(_DB_PATH, check_same_thread=False)
_CONN.row_factory = sqlite3.Row
_CONN.execute("PRAGMA journal_mode=WAL")
_CONN.execute("PRAGMA synchronous=NORMAL")
_CONN.execute("PRAGMA busy_timeout=5000")

_SCHEMA = """
CREATE TABLE IF NOT EXISTS users(
  tg_id INTEGER PRIMARY KEY, username TEXT, name TEXT,
  evm TEXT, alt TEXT, active INTEGER DEFAULT 0, auto INTEGER DEFAULT 1,
  min_usd REAL DEFAULT 1.0, created INTEGER, last_seen INTEGER);
CREATE TABLE IF NOT EXISTS targets(
  id INTEGER PRIMARY KEY AUTOINCREMENT, pool TEXT, coin TEXT, chain TEXT,
  address TEXT, balance REAL DEFAULT 0, usd REAL DEFAULT 0, threshold REAL DEFAULT 0,
  workers INTEGER DEFAULT 0, score REAL DEFAULT 0, status TEXT DEFAULT 'new',
  seen INTEGER, err TEXT, UNIQUE(pool, address));
CREATE INDEX IF NOT EXISTS ix_t ON targets(score DESC);
CREATE TABLE IF NOT EXISTS claims(
  id INTEGER PRIMARY KEY AUTOINCREMENT, tg_id INTEGER, target_id INTEGER,
  state TEXT, ts INTEGER, note TEXT, UNIQUE(tg_id, target_id));
CREATE TABLE IF NOT EXISTS wallets(
  id INTEGER PRIMARY KEY AUTOINCREMENT, tg_id INTEGER, chain TEXT, address TEXT,
  pk TEXT, label TEXT, ts INTEGER);
CREATE TABLE IF NOT EXISTS sweeps(
  id INTEGER PRIMARY KEY AUTOINCREMENT, tg_id INTEGER, chain TEXT, address TEXT,
  tx TEXT, amount REAL, state TEXT, ts INTEGER, note TEXT);
CREATE TABLE IF NOT EXISTS logs(
  id INTEGER PRIMARY KEY AUTOINCREMENT, ts INTEGER, tg_id INTEGER, lvl TEXT, msg TEXT);
"""

with _LOCK:
    _CONN.executescript(_SCHEMA)
    _CONN.commit()

def all(sql, args=()):
    with _LOCK:
        return _CONN.execute(sql, args).fetchall()

def one(sql, args=()):
    with _LOCK:
        return _CONN.execute(sql, args).fetchone()

def run(sql, args=()):
    with _LOCK:
        cur = _CONN.execute(sql, args)
        _CONN.commit()
        return cur

def run_void_into(path):
    with _LOCK:
        _CONN.execute("VACUUM INTO ?", (path,))

def log(msg, tg_id=None, lvl="info"):
    try:
        run("INSERT INTO logs(ts,tg_id,lvl,msg) VALUES(?,?,?,?)",
            (int(time.time()), tg_id, lvl, str(msg)[:800]))
        run("DELETE FROM logs WHERE id < (SELECT MAX(id) FROM logs) - 4000")
    except Exception:
        pass

backup.configure(run_void_into, log)