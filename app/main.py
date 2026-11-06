import hashlib
import html
import time
import hmac
import io
import json
import logging
import secrets
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from fastapi import Body, Depends, FastAPI, File, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse
from PIL import Image, ImageOps

from . import backup, db, google, scheduler, thumbs
from .config import APP_PASSWORD, BASE_URL, DATA_DIR, DRIVE_MODE, TEST_MODE, TIMEZONE
from .web import DASHBOARD, LOGIN

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
TZ = ZoneInfo(TIMEZONE)
app = FastAPI(title="NMR YouTube Scheduler")
COOKIE = "nmr_auth"


def _cookie_value():
    return hmac.new(APP_PASSWORD.encode(), b"nmr-scheduler", hashlib.sha256).hexdigest()


def _authed(request: Request):
    if not APP_PASSWORD:
        return True
    auth = request.headers.get("authorization", "")
    if auth.startswith("Bearer ") and hmac.compare_digest(auth[7:], APP_PASSWORD):
        return True
    c = request.cookies.get(COOKIE)
    return bool(c) and hmac.compare_digest(c, _cookie_value())


def need_auth(request: Request):
    if not _authed(request):
        raise HTTPException(401, "wrong or missing app password")


@app.on_event("startup")
def _start():
    if not APP_PASSWORD:
        logging.warning("APP_PASSWORD is empty: anyone with the address can use this server")
    scheduler.start()
    thumbs.start()
    backup.start()


# ---------- pages ----------

@app.get("/", response_class=HTMLResponse)
def home(request: Request):
    if not _authed(request):
        return HTMLResponse(LOGIN.replace("{{error}}", ""))
    return HTMLResponse(DASHBOARD.replace("{{tz}}", TIMEZONE).replace("{{test}}", "1" if TEST_MODE else "0"))


@app.post("/login")
async def login(request: Request):
    form = await request.form()
    if not hmac.compare_digest(str(form.get("password", "")), APP_PASSWORD):
        return HTMLResponse(LOGIN.replace("{{error}}", "Wrong password"), status_code=401)
    r = RedirectResponse("/", status_code=303)
    r.set_cookie(COOKIE, _cookie_value(), httponly=True, secure=BASE_URL.startswith("https"),
                 samesite="lax", max_age=3600 * 24 * 365)
    return r


@app.get("/connect", dependencies=[Depends(need_auth)])
def connect():
    return _start_login("youtube")


@app.get("/connect-drive", dependencies=[Depends(need_auth)])
def connect_drive():
    return _start_login("drive")


@app.post("/api/connect-link", dependencies=[Depends(need_auth)])
def connect_link(data: dict = Body(default={})):
    """One-time link (10 min) so the phone app can open Google sign-in in a browser without logging in there."""
    kind = "drive" if data.get("kind") == "drive" else "youtube"
    now = time.time()
    links = {c: v for c, v in (db.kv_get("connect_links") or {}).items() if v["exp"] > now}
    code = secrets.token_urlsafe(18)
    links[code] = {"kind": kind, "exp": now + 600}
    db.kv_set("connect_links", links)
    return {"url": f"{BASE_URL}/connect-start?code={code}"}


@app.get("/connect-start")
def connect_start(code: str = ""):
    # Reusable until it expires: browsers sometimes open the same link twice.
    link = (db.kv_get("connect_links") or {}).get(code)
    if not link or link["exp"] < time.time():
        return HTMLResponse("<p>This link has expired. Tap the button in the app again.</p>", status_code=400)
    return _start_login(link["kind"])


DONE_PAGE = """<!doctype html><html><head><meta name="viewport" content="width=device-width,initial-scale=1">
<style>body{font-family:system-ui,sans-serif;background:#0f1115;color:#e8e8e8;text-align:center;padding:60px 20px}
a{color:#90caf9}</style></head><body><h2>&#10003; {what} connected</h2>
<p>You can close this tab and go back to the app.</p><p><a href="/">Open the web dashboard</a></p></body></html>"""


def _start_login(kind):
    state = secrets.token_urlsafe(24)
    db.kv_set("oauth_state", {"state": state, "kind": kind})
    return RedirectResponse(google.auth_url(state, kind))


@app.get("/oauth/callback")
def oauth_callback(code: str = "", state: str = "", error: str = ""):
    if error:
        return HTMLResponse(f"<p>Google said: {error}. <a href='/'>Back</a></p>", status_code=400)
    saved = db.kv_get("oauth_state") or {}
    if not state or state != saved.get("state"):
        return HTMLResponse("<p>Login link expired. <a href='/'>Back</a></p>", status_code=400)
    kind = saved.get("kind", "youtube")
    db.kv_set("oauth_state", None)
    tok = google.exchange_code(code)
    retry = "/connect-drive" if kind == "drive" else "/connect"
    if "refresh_token" not in tok:
        return HTMLResponse("<p>Google did not give a long-term login. Remove NMR Scheduler at "
                            "myaccount.google.com/permissions and <a href='" + retry + "'>connect again</a>.</p>")
    granted = tok.get("scope", "")
    need = "drive.file" if kind == "drive" else "youtube.upload"
    if need not in granted:
        return HTMLResponse("<p>Please tick every permission box on Google's screen. "
                            "<a href='" + retry + "'>Try again</a></p>", status_code=400)
    email = google.userinfo_email(tok["access_token"])

    if kind == "drive":
        row = db.q("SELECT id FROM accounts WHERE email=? ORDER BY channel_id IS NULL, id", (email,), one=True)
        if row:
            acc_id = row["id"]
        else:
            acc_id = db.x("INSERT INTO accounts(email, created_at) VALUES(?, ?)", (email, db.now_iso()))
        db.update("accounts", acc_id, drive_refresh_token=tok["refresh_token"], needs_reconnect=0)
        if not db.q("SELECT id FROM accounts WHERE is_storage=1 AND drive_refresh_token IS NOT NULL", one=True):
            db.x("UPDATE accounts SET is_storage=0")
            db.update("accounts", acc_id, is_storage=1)
        google.access_token(acc_id, force=True, kind="drive")
        return HTMLResponse(DONE_PAGE.replace("{what}", "Google Drive"))

    ch = google.my_channel(tok["access_token"])
    if not ch:
        return HTMLResponse("<p>This Google account has no YouTube channel. "
                            "<a href='/connect'>Try another account</a></p>", status_code=400)
    existing = db.q("SELECT id FROM accounts WHERE channel_id=?", (ch["id"],), one=True)
    if not existing:  # a Drive-only row for the same Gmail can take the channel too
        existing = db.q("SELECT id FROM accounts WHERE email=? AND channel_id IS NULL", (email,), one=True)
    fields = dict(email=email, refresh_token=tok["refresh_token"], needs_reconnect=0,
                  channel_id=ch["id"], channel_title=ch["title"], channel_thumb=ch["thumb"])
    if existing:
        acc_id = existing["id"]
    else:
        acc_id = db.x("INSERT INTO accounts(email, created_at) VALUES(?, ?)", (email, db.now_iso()))
    db.update("accounts", acc_id, **fields)
    google.access_token(acc_id, force=True)
    return HTMLResponse(DONE_PAGE.replace("{what}", "YouTube channel " + html.escape(ch["title"])))


# ---------- helpers ----------

def _local(iso):
    if not iso:
        return None
    return datetime.fromisoformat(iso).astimezone(TZ).strftime("%Y-%m-%d %H:%M")


def _parse_time(s):
    """Accepts '2026-11-02 18:00', '2026-11-02T18:00' (server timezone) or full ISO with offset."""
    if not s:
        return None
    try:
        dt = datetime.fromisoformat(str(s).strip().replace(" ", "T"))
    except ValueError:
        raise HTTPException(400, f"bad time '{s}', use YYYY-MM-DD HH:MM")
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=TZ)
    return dt.astimezone(timezone.utc).replace(microsecond=0).isoformat()


def _video_out(v):
    ch = db.q("SELECT channel_title FROM accounts WHERE id=?", (v["channel_account_id"],), one=True)
    try:
        tags = json.loads(v["tags"] or "[]")
    except Exception:
        tags = []
    return {
        "id": v["id"], "status": v["status"], "title": v["title"], "description": v["description"],
        "tags": tags, "category_id": v["category_id"], "made_for_kids": bool(v["made_for_kids"]),
        "synthetic_media": bool(v["synthetic_media"]), "playlist_id": v["playlist_id"],
        "channel_account_id": v["channel_account_id"], "channel": ch and ch["channel_title"],
        "publish_at": v["publish_at"], "publish_at_local": _local(v["publish_at"]),
        "posted_at_local": _local(v["posted_at"]), "file_name": v["file_name"], "file_size": v["file_size"],
        "youtube_url": v["youtube_id"] and f"https://youtu.be/{v['youtube_id']}",
        "has_thumbnail": bool(v["thumb_path"] and Path(v["thumb_path"]).exists()),
        "error": v["error"], "attempts": v["attempts"], "created_at": v["created_at"],
        "thumb_state": v.get("thumb_state"), "thumb_note": v.get("thumb_note"), "thumb_pick": v.get("thumb_pick"),
        "thumb_by": v.get("thumb_by"), "thumb_options": len(json.loads(v.get("thumb_options") or "[]")),
        "thumb_deadline_local": _local(dl.isoformat()) if (dl := thumbs.deadline(v)) else None,
        "meta_state": v.get("meta_state"), "meta_note": v.get("meta_note"),
        "title_options": json.loads(v.get("meta_titles") or "[]"),
        "option_prompts": [thumbs.option_prompt(v, k) for k in range(1, len(json.loads(v.get("thumb_options") or "[]")) + 1)],
        "extra_prompts": json.loads(v.get("extra_prompts") or "[]"),
        "prompts_state": v.get("prompts_state"), "prompts_note": v.get("prompts_note"),
        "thumb_version": int(Path(v["thumb_path"]).stat().st_mtime) if v["thumb_path"] and Path(v["thumb_path"]).exists() else 0,
    }


def _get_video(vid):
    v = db.q("SELECT * FROM videos WHERE id=?", (vid,), one=True)
    if not v:
        raise HTTPException(404, "no such video")
    return v


def _storage_account_for(channel_account_id):
    if DRIVE_MODE == "per_channel":
        ch = db.q("SELECT email FROM accounts WHERE id=?", (channel_account_id,), one=True)
        own = ch and db.q("SELECT id FROM accounts WHERE email=? AND drive_refresh_token IS NOT NULL",
                          (ch["email"],), one=True)
        if own:
            return own["id"]
    s = db.q("SELECT id FROM accounts WHERE is_storage=1 AND drive_refresh_token IS NOT NULL", one=True)
    if not s:
        raise HTTPException(409, "No Drive connected yet. Open the scheduler page and tap 'Connect Google Drive'.")
    return s["id"]


EDITABLE = {"title", "description", "tags", "category_id", "made_for_kids", "synthetic_media",
            "playlist_id", "publish_at", "channel_account_id"}


def _clean(data):
    out = {}
    for k, val in data.items():
        if k not in EDITABLE:
            continue
        if k == "tags":
            if isinstance(val, str):
                val = [t.strip() for t in val.split(",")]
            val = json.dumps([t for t in val if t])
        elif k in ("made_for_kids", "synthetic_media"):
            val = 1 if val else 0
        elif k == "publish_at":
            val = _parse_time(val)
        elif k == "title" and val is not None:
            val = str(val)[:100]
        out[k] = val
    return out


# ---------- API ----------

api = Depends(need_auth)


@app.post("/api/backup-now", dependencies=[Depends(need_auth)])
def backup_now():
    try:
        return {"ok": True, "backup": backup.run(), "last": db.kv_get("last_backup")}
    except google.GoogleError as e:
        raise HTTPException(502, f"Drive refused the backup: {str(e)[:200]}")


@app.get("/api/health")
def health():
    return {"ok": True, "test_mode": TEST_MODE, "timezone": TIMEZONE, "time": db.now_iso()}


@app.get("/api/channels", dependencies=[api])
def channels():
    rows = db.q("SELECT id, email, channel_id, channel_title, channel_thumb, is_storage, needs_reconnect, "
                "drive_refresh_token IS NOT NULL AS has_drive FROM accounts ORDER BY id")
    return [dict(r, is_storage=bool(r["is_storage"]), needs_reconnect=bool(r["needs_reconnect"]),
                 has_drive=bool(r["has_drive"])) for r in rows]


@app.post("/api/accounts/{acc_id}/storage", dependencies=[api])
def set_storage(acc_id: int):
    acc = db.q("SELECT drive_refresh_token FROM accounts WHERE id=?", (acc_id,), one=True)
    if not acc or not acc["drive_refresh_token"]:
        raise HTTPException(400, "connect Google Drive for this Gmail first")
    db.x("UPDATE accounts SET is_storage=0")
    db.update("accounts", acc_id, is_storage=1)
    return {"ok": True}


@app.delete("/api/accounts/{acc_id}", dependencies=[api])
def remove_account(acc_id: int):
    db.x("DELETE FROM accounts WHERE id=?", (acc_id,))
    return {"ok": True}


@app.get("/api/channels/{acc_id}/playlists", dependencies=[api])
def channel_playlists(acc_id: int):
    return google.playlists(acc_id)


@app.post("/api/drive/token", dependencies=[api])
def drive_token(data: dict = Body(default={})):
    """Short-lived token so the PC can upload straight into Drive. The long-term login never leaves the server."""
    ch_id = data.get("channel_account_id")
    if not ch_id:
        raise HTTPException(400, "channel_account_id required")
    store = _storage_account_for(int(ch_id))
    return {"access_token": google.access_token(store, kind="drive"), "drive_account_id": store,
            "folder_id": google.ensure_drive_folder(store)}


@app.get("/api/videos", dependencies=[api])
def list_videos(status: str = ""):
    if status:
        sts = status.split(",")
        rows = db.q(f"SELECT * FROM videos WHERE status IN ({','.join('?' * len(sts))}) "
                    "ORDER BY COALESCE(publish_at, created_at)", tuple(sts))
    else:
        rows = db.q("SELECT * FROM videos ORDER BY COALESCE(publish_at, created_at)")
    return [_video_out(v) for v in rows]


@app.post("/api/videos", dependencies=[api])
def add_video(data: dict = Body(...)):
    """rendering=true: the video is still being made in AutoDoc; titles/thumbnails start now and the file is
    attached later with /attach-file."""
    rendering = bool(data.get("rendering"))
    for k in ("channel_account_id",) if rendering else ("channel_account_id", "drive_file_id", "drive_account_id"):
        if not data.get(k):
            raise HTTPException(400, f"{k} required")
    fields = _clean(data)
    vid = db.x("INSERT INTO videos(channel_account_id, drive_account_id, drive_file_id, file_name, file_size, "
               "created_at) VALUES(?,?,?,?,?,?)",
               (data["channel_account_id"], data.get("drive_account_id"), data.get("drive_file_id"),
                data.get("file_name"), data.get("file_size"), db.now_iso()))
    fields["status"] = "rendering" if rendering else ("scheduled" if fields.get("publish_at") else "draft")
    db.update("videos", vid, **fields)
    if data.get("auto_thumbnail"):
        thumbs.start_job(vid)
    if (data.get("script") or "").strip():
        thumbs.start_meta(vid, data["script"])
    scheduler.wake()
    return _video_out(_get_video(vid))


@app.get("/api/videos/{vid}", dependencies=[api])
def get_video(vid: int):
    return _video_out(_get_video(vid))


@app.patch("/api/videos/{vid}", dependencies=[api])
def edit_video(vid: int, data: dict = Body(...)):
    v = _get_video(vid)
    if v["status"] == "posting":
        raise HTTPException(409, "uploading right now; edit it when it's posted")
    fields = _clean(data)
    if v["status"] == "posted":
        # only the words can change now; they're updated on YouTube first
        fields = {k: val for k, val in fields.items() if k in ("title", "description", "tags")}
        changed = {k: val for k, val in fields.items() if val != v[k]}
        if changed and v["youtube_id"]:
            from .transfer import update_youtube_details
            try:
                update_youtube_details(v, title=changed.get("title"), description=changed.get("description"),
                                       tags=json.loads(changed["tags"]) if "tags" in changed else None)
            except google.GoogleError as e:
                raise HTTPException(502, f"YouTube didn't accept the change: {str(e)[:200]}")
        db.update("videos", vid, **changed)
        return _video_out(_get_video(vid))
    if "publish_at" in fields:
        fields["status"] = "rendering" if v["status"] == "rendering" else ("scheduled" if fields["publish_at"] else "draft")
        fields.update(attempts=0, next_try_at=None, error=None)
    db.update("videos", vid, **fields)
    scheduler.wake()
    return _video_out(_get_video(vid))


@app.delete("/api/videos/{vid}", dependencies=[api])
def delete_video(vid: int, keep_drive: bool = False):
    v = _get_video(vid)
    if v["status"] == "posting":
        raise HTTPException(409, "posting right now, try again in a few minutes")
    if not keep_drive and v["status"] != "posted" and v["drive_file_id"]:
        try:
            google.call(v["drive_account_id"], "DELETE", f"{google.DRIVE}/files/{v['drive_file_id']}",
                        params={"supportsAllDrives": "true"})
        except google.GoogleError as e:
            if e.status != 404:
                raise HTTPException(502, f"could not delete from Drive: {e}")
    if v["thumb_path"]:
        Path(v["thumb_path"]).unlink(missing_ok=True)
    import shutil
    shutil.rmtree(thumbs.OPT_DIR / str(vid), ignore_errors=True)
    (thumbs.SCRIPT_DIR / f"{vid}.txt").unlink(missing_ok=True)
    db.x("DELETE FROM videos WHERE id=?", (vid,))
    return {"ok": True}


@app.post("/api/videos/{vid}/post-now", dependencies=[api])
def post_now(vid: int):
    v = _get_video(vid)
    if v["status"] in ("posting", "posted"):
        raise HTTPException(409, "already posting or posted")
    if v["status"] == "rendering":
        raise HTTPException(409, "the video is still being made in AutoDoc")
    db.update("videos", vid, status="scheduled", publish_at=db.now_iso(), attempts=0, next_try_at=None, error=None)
    scheduler.wake()
    return _video_out(_get_video(vid))


@app.post("/api/videos/{vid}/retry", dependencies=[api])
def retry(vid: int):
    v = _get_video(vid)
    if v["status"] != "failed":
        raise HTTPException(409, "only failed videos can be retried")
    db.update("videos", vid, status="scheduled", attempts=0, next_try_at=None, error=None)
    scheduler.wake()
    return _video_out(_get_video(vid))


@app.post("/api/videos/{vid}/thumbnail", dependencies=[api])
async def set_thumbnail(vid: int, file: UploadFile = File(...)):
    _get_video(vid)
    img = Image.open(io.BytesIO(await file.read()))
    img = ImageOps.exif_transpose(img).convert("RGB")
    img = ImageOps.fit(img, (1280, 720), Image.Resampling.LANCZOS)  # crop to 16:9 like YouTube
    path = DATA_DIR / "thumbs" / f"{vid}.jpg"
    for quality in (92, 85, 75, 65):
        buf = io.BytesIO()
        img.save(buf, "JPEG", quality=quality, optimize=True)
        if buf.tell() < 2 * 1024 * 1024:
            break
    path.write_bytes(buf.getvalue())
    v = _get_video(vid)
    extra = {}
    if v.get("thumb_state") in ("prompting", "queued", "ready"):
        extra = {"thumb_state": None, "thumb_note": "your own thumbnail is used"}
    db.update("videos", vid, thumb_path=str(path), **extra)
    if v["status"] == "posted" and v["youtube_id"]:
        from .transfer import set_youtube_thumbnail
        err = set_youtube_thumbnail(v, v["youtube_id"], path)
        if err:
            raise HTTPException(502, f"saved, but YouTube refused it: {err}")
    return _video_out(_get_video(vid))


@app.get("/api/videos/{vid}/thumbnail", dependencies=[api])
def get_thumbnail(vid: int):
    v = _get_video(vid)
    if not v["thumb_path"] or not Path(v["thumb_path"]).exists():
        raise HTTPException(404, "no thumbnail")
    return FileResponse(v["thumb_path"], media_type="image/jpeg")


# ---------- Flow thumbnails ----------

@app.post("/api/videos/{vid}/auto-thumbnail", dependencies=[api])
def auto_thumbnail(vid: int):
    """Make (or remake) Flow thumbnail options. Works for posted videos too."""
    v = _get_video(vid)
    if v.get("thumb_state") == "generating":
        raise HTTPException(409, "Flow is making this one right now")
    thumbs.start_job(vid)
    return _video_out(_get_video(vid))


@app.get("/api/videos/{vid}/thumb-options/{n}", dependencies=[api])
def thumb_option(vid: int, n: int):
    opts = json.loads(_get_video(vid).get("thumb_options") or "[]")
    if not 1 <= n <= len(opts) or not Path(opts[n - 1]).exists():
        raise HTTPException(404, "no such option")
    return FileResponse(opts[n - 1], media_type="image/jpeg")


@app.post("/api/videos/{vid}/thumb-pick", dependencies=[api])
def thumb_pick(vid: int, data: dict = Body(...)):
    _get_video(vid)
    try:
        thumbs.apply_pick(vid, int(data.get("option", 0)), by="you")
    except ValueError as e:
        raise HTTPException(400, str(e))
    return _video_out(_get_video(vid))


@app.get("/api/thumb-style/{acc_id}", dependencies=[api])
def get_thumb_style(acc_id: int):
    return {"style": thumbs.app_style(acc_id), "footer": thumbs.desc_footer(acc_id)}


@app.put("/api/thumb-style/{acc_id}", dependencies=[api])
def put_thumb_style(acc_id: int, data: dict = Body(...)):
    db.kv_set(f"thumb_style:{acc_id}", str(data.get("style", ""))[:1500])
    if "footer" in data:
        db.kv_set(f"desc_footer:{acc_id}", str(data.get("footer") or "")[:3000])
    return {"ok": True}


@app.get("/api/events", dependencies=[api])
def events(after: int = 0):
    """Things the phone should notify about (thumbnails ready, Claude picked, Flow needs you)."""
    return [e for e in db.kv_get("events", []) if e["seq"] > after]


@app.get("/api/flow/alert", dependencies=[api])
def flow_alert():
    return db.kv_get("flow_alert", None) or {}


# The Flow extension runs in Chrome on this same server.
@app.get("/api/flow/next", dependencies=[api])
def flow_next():
    return thumbs.next_flow_job() or {}


@app.post("/api/flow/done", dependencies=[api])
def flow_done(data: dict = Body(...)):
    return thumbs.flow_done(str(data.get("job", "")), data.get("images"))


@app.get("/api/flow/command", dependencies=[api])
def flow_command():
    return thumbs.take_flow_command()


@app.post("/api/videos/{vid}/retry-flow", dependencies=[api])
def retry_flow(vid: int):
    v = _get_video(vid)
    if v.get("thumb_state") not in ("generating", "queued", "failed"):
        raise HTTPException(409, "nothing to retry; use Remake or + 3 more")
    thumbs.retry_flow(vid)
    return _video_out(_get_video(vid))


@app.post("/api/flow/status", dependencies=[api])
def flow_status(data: dict = Body(...)):
    thumbs.flow_status(str(data.get("job") or ""), str(data.get("status", "")), str(data.get("message") or ""))
    return {"ok": True}


@app.post("/api/videos/{vid}/script", dependencies=[api])
def add_script(vid: int, data: dict = Body(...)):
    """(Re)write title options + description (+ thumbnail prompts if a Flow job is waiting) from a script."""
    _get_video(vid)
    if not (data.get("script") or "").strip():
        raise HTTPException(400, "script required")
    thumbs.start_meta(vid, data["script"])
    return _video_out(_get_video(vid))


@app.post("/api/videos/{vid}/more-titles", dependencies=[api])
def more_titles(vid: int):
    v = _get_video(vid)
    if v.get("meta_state") in ("summarizing", "writing", "more"):
        raise HTTPException(409, "Claude is already writing titles for this video")
    thumbs.start_more_titles(vid)
    return _video_out(_get_video(vid))


@app.post("/api/videos/{vid}/more-thumbnails", dependencies=[api])
def more_thumbnails(vid: int, data: dict = Body(default={})):
    """kind=new (3 new ideas), like + option (3 like that one), edit + option + note (1 edited copy)."""
    v = _get_video(vid)
    if v.get("thumb_state") in ("prompting", "queued", "generating"):
        raise HTTPException(409, "thumbnails are already being made for this video; try again when they're done")
    try:
        thumbs.start_more_thumbs(vid, str(data.get("kind") or "new"), int(data["option"]) if data.get("option") else None,
                                 str(data.get("note") or ""))
    except (ValueError, KeyError) as e:
        raise HTTPException(400, str(e))
    return _video_out(_get_video(vid))


# ---------- Claude connector (remote MCP) ----------
import sys as _sys  # noqa: E402
from . import mcp_server  # noqa: E402

mcp_server.register(app, _sys.modules[__name__])


@app.post("/api/videos/{vid}/more-prompts", dependencies=[api])
def more_prompts(vid: int, data: dict = Body(default={})):
    """Thumbnail prompts only, to paste into Flow by hand: kind=new (3 new ideas) or like + option (3 similar)."""
    v = _get_video(vid)
    if v.get("prompts_state") == "writing":
        raise HTTPException(409, "Claude is already writing prompts for this video")
    try:
        thumbs.start_more_prompts(vid, str(data.get("kind") or "new"), int(data["option"]) if data.get("option") else None)
    except ValueError as e:
        raise HTTPException(400, str(e))
    return _video_out(_get_video(vid))


@app.post("/api/videos/{vid}/attach-file", dependencies=[api])
def attach_file(vid: int, data: dict = Body(...)):
    """AutoDoc finished: the rendered file is in Drive now."""
    v = _get_video(vid)
    if v["status"] != "rendering":
        raise HTTPException(409, f"video is {v['status']}, not waiting for its file")
    for k in ("drive_file_id", "drive_account_id"):
        if not data.get(k):
            raise HTTPException(400, f"{k} required")
    db.update("videos", vid, drive_file_id=data["drive_file_id"], drive_account_id=int(data["drive_account_id"]),
              file_size=data.get("file_size"), status="scheduled" if v["publish_at"] else "draft")
    scheduler.wake()
    return _video_out(_get_video(vid))
