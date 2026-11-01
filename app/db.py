import json
import sqlite3
import threading
from datetime import datetime, timezone

from .config import DATA_DIR

_lock = threading.RLock()
_conn = sqlite3.connect(DATA_DIR / "scheduler.db", check_same_thread=False)
_conn.row_factory = sqlite3.Row

SCHEMA = """
CREATE TABLE IF NOT EXISTS accounts (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  email TEXT,
  channel_id TEXT,
  channel_title TEXT,
  channel_thumb TEXT,
  refresh_token TEXT,
  is_storage INTEGER DEFAULT 0,
  needs_reconnect INTEGER DEFAULT 0,
  drive_folder_id TEXT,
  created_at TEXT
);
CREATE TABLE IF NOT EXISTS videos (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  channel_account_id INTEGER,
  drive_account_id INTEGER,
  drive_file_id TEXT,
  file_name TEXT,
  file_size INTEGER,
  title TEXT,
  description TEXT DEFAULT '',
  tags TEXT DEFAULT '[]',
  category_id TEXT DEFAULT '27',
  made_for_kids INTEGER DEFAULT 0,
  synthetic_media INTEGER DEFAULT 0,
  playlist_id TEXT,
  publish_at TEXT,
  status TEXT DEFAULT 'draft',
  upload_url TEXT,
  youtube_id TEXT,
  thumb_path TEXT,
  error TEXT,
  attempts INTEGER DEFAULT 0,
  next_try_at TEXT,
  posted_at TEXT,
  created_at TEXT
);
CREATE TABLE IF NOT EXISTS kv (k TEXT PRIMARY KEY, v TEXT);
"""

with _lock:
    _conn.executescript(SCHEMA)
    cols = [r[1] for r in _conn.execute("PRAGMA table_info(accounts)")]
    if "drive_refresh_token" not in cols:
        _conn.execute("ALTER TABLE accounts ADD COLUMN drive_refresh_token TEXT")
    _conn.commit()


def now_iso():
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def q(sql, args=(), one=False):
    with _lock:
        cur = _conn.execute(sql, args)
        rows = [dict(r) for r in cur.fetchall()]
    return (rows[0] if rows else None) if one else rows


def x(sql, args=()):
    with _lock:
        cur = _conn.execute(sql, args)
        _conn.commit()
        return cur.lastrowid


def update(table, row_id, **fields):
    if not fields:
        return
    cols = ", ".join(f"{k}=?" for k in fields)
    x(f"UPDATE {table} SET {cols} WHERE id=?", (*fields.values(), row_id))


def kv_get(k, default=None):
    r = q("SELECT v FROM kv WHERE k=?", (k,), one=True)
    return json.loads(r["v"]) if r else default


def kv_set(k, v):
    x("INSERT INTO kv(k,v) VALUES(?,?) ON CONFLICT(k) DO UPDATE SET v=excluded.v", (k, json.dumps(v)))
