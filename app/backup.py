"""Daily backup to Google Drive: scheduler database, channel references and settings, as one .tar.gz in the
"NMR Scheduler" Drive folder. Keeps the last KEEP backups. The file contains the Google logins, so don't share it."""
import io
import json
import logging
import sqlite3
import tarfile
import tempfile
import threading
import time
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from . import db, google
from .config import DATA_DIR, ROOT, TIMEZONE

log = logging.getLogger("backup")
KEEP = 7
AT_HOUR = 3   # local time
TZ = ZoneInfo(TIMEZONE)


def _storage():
    s = db.q("SELECT id FROM accounts WHERE is_storage=1 AND drive_refresh_token IS NOT NULL", one=True)
    return s and s["id"]


def make_archive():
    buf = io.BytesIO()
    with tempfile.TemporaryDirectory() as tmp:
        snap = Path(tmp) / "scheduler.db"
        src = sqlite3.connect(DATA_DIR / "scheduler.db")
        dst = sqlite3.connect(snap)
        with dst:
            src.backup(dst)   # consistent copy even while the server writes
        src.close()
        dst.close()
        with tarfile.open(fileobj=buf, mode="w:gz") as tar:
            tar.add(snap, "data/scheduler.db")
            for p, arc in ((DATA_DIR / "refs", "data/refs"), (ROOT / ".env", ".env"),
                           (ROOT / "client_secret.json", "client_secret.json")):
                if p.exists():
                    tar.add(p, arc)
    return buf.getvalue()


def run():
    acc = _storage()
    if not acc:
        return "no Drive connected"
    folder = google.ensure_drive_folder(acc)
    data = make_archive()
    name = f"nmr-backup-{datetime.now(TZ):%Y-%m-%d}.tar.gz"
    boundary = "nmrbackup" + str(int(time.time()))
    meta = json.dumps({"name": name, "parents": [folder]})
    body = (f"--{boundary}\r\nContent-Type: application/json; charset=UTF-8\r\n\r\n{meta}\r\n"
            f"--{boundary}\r\nContent-Type: application/gzip\r\n\r\n").encode() + data + f"\r\n--{boundary}--".encode()
    google.call(acc, "POST", f"{google.DRIVE_UPLOAD}/files", params={"uploadType": "multipart", "fields": "id"},
                headers={"Content-Type": f"multipart/related; boundary={boundary}"}, data=body)
    # keep the newest KEEP backups
    q = f"'{folder}' in parents and name contains 'nmr-backup-' and trashed=false"
    files = google.call(acc, "GET", f"{google.DRIVE}/files", params={
        "q": q, "orderBy": "createdTime desc", "fields": "files(id,name)", "pageSize": 100}).json().get("files", [])
    for f in files[KEEP:]:
        google.call(acc, "DELETE", f"{google.DRIVE}/files/{f['id']}")
    db.kv_set("last_backup", {"at": db.now_iso(), "name": name, "kb": len(data) // 1024})
    log.info("backup %s uploaded (%d KB)", name, len(data) // 1024)
    return f"{name} ({len(data) // 1024} KB)"


def loop():
    while True:
        try:
            now = datetime.now(TZ)
            last = (db.kv_get("last_backup") or {}).get("name", "")
            if now.hour >= AT_HOUR and f"{now:%Y-%m-%d}" not in last:
                run()
        except Exception:  # noqa: BLE001
            log.exception("backup failed; will try again in an hour")
            time.sleep(3600)
        time.sleep(600)


def start():
    threading.Thread(target=loop, daemon=True, name="backup").start()
