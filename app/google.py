"""Google OAuth + thin REST helpers (YouTube Data API v3, Drive v3)."""
import json
import threading
import time
from urllib.parse import urlencode

import requests

from . import db
from .config import CLIENT_SECRET_FILE, DRIVE_FOLDER_NAME, REDIRECT_URI

AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_URL = "https://oauth2.googleapis.com/token"
YT = "https://www.googleapis.com/youtube/v3"
YT_UPLOAD = "https://www.googleapis.com/upload/youtube/v3"
DRIVE = "https://www.googleapis.com/drive/v3"
DRIVE_UPLOAD = "https://www.googleapis.com/upload/drive/v3"

# Google refuses YouTube and Drive scopes in one consent screen, so there are two sign-ins.
SCOPES = {
    "youtube": ["openid", "email", "https://www.googleapis.com/auth/youtube.upload",
                "https://www.googleapis.com/auth/youtube"],
    "drive": ["openid", "email", "https://www.googleapis.com/auth/drive.file"],
}


class GoogleError(Exception):
    def __init__(self, status, body):
        self.status = status
        self.body = body
        try:
            err = json.loads(body).get("error", {})
            reason = ""
            if isinstance(err, dict):
                errs = err.get("errors") or []
                reason = errs[0].get("reason", "") if errs else ""
                msg = err.get("message", body)
            else:
                msg = str(err)
        except Exception:
            reason, msg = "", body
        self.reason = reason
        super().__init__(f"{status} {reason}: {msg}"[:500])


def _client():
    data = json.loads(CLIENT_SECRET_FILE.read_text())
    return data.get("web") or data.get("installed")


def auth_url(state, kind="youtube"):
    c = _client()
    params = {
        "client_id": c["client_id"],
        "redirect_uri": REDIRECT_URI,
        "response_type": "code",
        "scope": " ".join(SCOPES[kind]),
        "access_type": "offline",
        "prompt": "consent select_account",
        "state": state,
    }
    return AUTH_URL + "?" + urlencode(params)


def exchange_code(code):
    c = _client()
    r = requests.post(TOKEN_URL, data={
        "code": code,
        "client_id": c["client_id"],
        "client_secret": c["client_secret"],
        "redirect_uri": REDIRECT_URI,
        "grant_type": "authorization_code",
    }, timeout=30)
    if r.status_code != 200:
        raise GoogleError(r.status_code, r.text)
    return r.json()


_cache = {}
_cache_lock = threading.Lock()


def access_token(account_id, force=False, kind="youtube"):
    key = (account_id, kind)
    with _cache_lock:
        hit = _cache.get(key)
        if hit and not force and hit[1] > time.time() + 120:
            return hit[0]
    acc = db.q("SELECT * FROM accounts WHERE id=?", (account_id,), one=True)
    if not acc:
        raise GoogleError(404, '{"error":"account not found"}')
    refresh = acc["drive_refresh_token"] if kind == "drive" else acc["refresh_token"]
    if not refresh:
        raise GoogleError(400, json.dumps({"error": f"{kind} not connected for this account"}))
    c = _client()
    r = requests.post(TOKEN_URL, data={
        "client_id": c["client_id"],
        "client_secret": c["client_secret"],
        "refresh_token": refresh,
        "grant_type": "refresh_token",
    }, timeout=30)
    if r.status_code != 200:
        if "invalid_grant" in r.text:
            db.update("accounts", account_id, needs_reconnect=1)
        raise GoogleError(r.status_code, r.text)
    tok = r.json()
    with _cache_lock:
        _cache[key] = (tok["access_token"], time.time() + tok.get("expires_in", 3600))
    if acc["needs_reconnect"]:
        db.update("accounts", account_id, needs_reconnect=0)
    return tok["access_token"]


def call(account_id, method, url, *, ok=(200, 201, 204), headers=None, **kw):
    """Authenticated request; refreshes once on 401. Drive URLs use the Drive login."""
    kind = "drive" if "/drive/" in url else "youtube"
    for attempt in (0, 1):
        h = {"Authorization": "Bearer " + access_token(account_id, force=attempt == 1, kind=kind)}
        h.update(headers or {})
        r = requests.request(method, url, headers=h, timeout=kw.pop("timeout", 120), **kw)
        if r.status_code == 401 and attempt == 0:
            continue
        if r.status_code not in ok:
            raise GoogleError(r.status_code, r.text)
        return r


def userinfo_email(token):
    r = requests.get("https://openidconnect.googleapis.com/v1/userinfo",
                     headers={"Authorization": "Bearer " + token}, timeout=30)
    return r.json().get("email") if r.ok else None


def my_channel(token):
    r = requests.get(YT + "/channels", params={"part": "snippet", "mine": "true"},
                     headers={"Authorization": "Bearer " + token}, timeout=30)
    if not r.ok:
        raise GoogleError(r.status_code, r.text)
    items = r.json().get("items", [])
    if not items:
        return None
    it = items[0]
    thumbs = it["snippet"].get("thumbnails", {})
    thumb = (thumbs.get("default") or {}).get("url")
    return {"id": it["id"], "title": it["snippet"]["title"], "thumb": thumb}


def playlists(account_id):
    out, token = [], None
    while True:
        params = {"part": "snippet", "mine": "true", "maxResults": 50}
        if token:
            params["pageToken"] = token
        data = call(account_id, "GET", YT + "/playlists", params=params).json()
        out += [{"id": p["id"], "title": p["snippet"]["title"]} for p in data.get("items", [])]
        token = data.get("nextPageToken")
        if not token:
            return out


def ensure_drive_folder(account_id):
    """Folder created by this app, so the drive.file scope can see it."""
    acc = db.q("SELECT drive_folder_id FROM accounts WHERE id=?", (account_id,), one=True)
    fid = acc and acc["drive_folder_id"]
    if fid:
        try:
            meta = call(account_id, "GET", f"{DRIVE}/files/{fid}", params={"fields": "id,trashed"}).json()
            if not meta.get("trashed"):
                return fid
        except GoogleError:
            pass
    meta = call(account_id, "POST", f"{DRIVE}/files", params={"fields": "id"},
                json={"name": DRIVE_FOLDER_NAME, "mimeType": "application/vnd.google-apps.folder"}).json()
    db.update("accounts", account_id, drive_folder_id=meta["id"])
    return meta["id"]
