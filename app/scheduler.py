import logging
import threading
import time
from datetime import datetime, timedelta, timezone

from . import db
from .google import GoogleError
from .transfer import after_upload, upload_to_youtube

log = logging.getLogger("scheduler")
MAX_ATTEMPTS = 3
_wake = threading.Event()


def wake():
    _wake.set()


def _due():
    now = db.now_iso()
    return db.q("""SELECT * FROM videos WHERE status='scheduled' AND publish_at <= ?
                   AND (next_try_at IS NULL OR next_try_at <= ?)
                   ORDER BY publish_at LIMIT 1""", (now, now), one=True)


def post_one(v):
    db.update("videos", v["id"], status="posting", error=None)
    try:
        vid = upload_to_youtube(v)
        db.update("videos", v["id"], youtube_id=vid, status="posting", error="setting thumbnail/playlist")
        note = after_upload(v, vid)
        db.update("videos", v["id"], status="posted", posted_at=db.now_iso(), error=note, upload_url=None)
        log.info("posted video %s as %s", v["id"], vid)
    except Exception as e:  # noqa: BLE001
        attempts = (v["attempts"] or 0) + 1
        msg = str(e)[:500]
        quota = isinstance(e, GoogleError) and e.reason in ("quotaExceeded", "uploadLimitExceeded", "dailyLimitExceeded")
        if attempts >= MAX_ATTEMPTS and not quota:
            db.update("videos", v["id"], status="failed", attempts=attempts, error=msg)
        else:
            delay = timedelta(hours=6) if quota else timedelta(minutes=10 * attempts)
            nxt = (datetime.now(timezone.utc) + delay).replace(microsecond=0).isoformat()
            db.update("videos", v["id"], status="scheduled", attempts=attempts, next_try_at=nxt,
                      error=f"retrying at {nxt}: {msg}")
        log.exception("posting video %s failed", v["id"])


def cleanup():
    """Privacy policy promise: posted-video records are deleted after 30 days."""
    cutoff = (datetime.now(timezone.utc) - timedelta(days=30)).replace(microsecond=0).isoformat()
    for v in db.q("SELECT id, thumb_path FROM videos WHERE status='posted' AND posted_at < ?", (cutoff,)):
        if v["thumb_path"]:
            from pathlib import Path
            Path(v["thumb_path"]).unlink(missing_ok=True)
        import shutil
        from .thumbs import OPT_DIR
        shutil.rmtree(OPT_DIR / str(v["id"]), ignore_errors=True)
        db.x("DELETE FROM videos WHERE id=?", (v["id"],))


def loop():
    # anything interrupted mid-upload resumes from its saved upload session
    db.x("UPDATE videos SET status='scheduled' WHERE status='posting'")
    while True:
        try:
            cleanup()
            v = _due()
            if v:
                post_one(v)
                continue
        except Exception:  # noqa: BLE001
            log.exception("scheduler loop error")
        _wake.wait(20)
        _wake.clear()


def start():
    threading.Thread(target=loop, daemon=True, name="scheduler").start()
