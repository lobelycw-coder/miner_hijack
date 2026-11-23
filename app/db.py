"""SQLite store on the data dir. WAL mode, single writer lock, restore hook."""
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
  id INTEGER PRIMARY KEY AUTOINCREMENT,