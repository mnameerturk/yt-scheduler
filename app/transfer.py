"""Streams a video from Google Drive to YouTube in chunks (resumable on both sides)."""
import json
import logging
import time
from pathlib import Path

import requests

from . import db
from .config import CHUNK, TEST_MODE, UPLOAD_PRIVACY
from .google import DRIVE, YT, YT_UPLOAD, GoogleError, access_token, call

log = logging.getLogger("transfer")


def _tags(v):
    try:
        return [t for t in json.loads(v["tags"] or "[]") if t][:30]
    except Exception:
        return []


def _create_session(v, total):
    body = {
        "snippet": {
            "title": (v["title"] or v["file_name"] or "Untitled")[:100],
            "description": (v["description"] or "")[:5000],
            "tags": _tags(v),
            "categoryId": v["category_id"] or "27",
        },
        "status": {
            "privacyStatus": UPLOAD_PRIVACY if UPLOAD_PRIVACY in ("public", "unlisted", "private")
            else ("private" if TEST_MODE else "public"),
            "selfDeclaredMadeForKids": bool(v["made_for_kids"]),
            "containsSyntheticMedia": bool(v["synthetic_media"]),
        },
    }
    r = call(v["channel_account_id"], "POST", YT_UPLOAD + "/videos",
             params={"uploadType": "resumable", "part": "snippet,status"},
             headers={"X-Upload-Content-Length": str(total), "X-Upload-Content-Type": "video/*",
                      "Content-Type": "application/json; charset=UTF-8"},
             data=json.dumps(body))
    url = r.headers["Location"]
    db.update("videos", v["id"], upload_url=url)
    return url


def _session_offset(v, url, total):
    """Returns next byte to send, -1 if session is dead, or a dict if already finished."""
    tok = access_token(v["channel_account_id"])
    r = requests.put(url, headers={"Authorization": "Bearer " + tok, "Content-Range": f"bytes */{total}",
                                   "Content-Length": "0"}, timeout=60)
    if r.status_code in (200, 201):
        return r.json()
    if r.status_code == 308:
        rng = r.headers.get("Range")
        return int(rng.split("-")[1]) + 1 if rng else 0
    return -1


def _drive_chunk(v, start, end):
    return call(v["drive_account_id"], "GET", f"{DRIVE}/files/{v['drive_file_id']}",
                params={"alt": "media", "supportsAllDrives": "true"},
                headers={"Range": f"bytes={start}-{end}"}, ok=(200, 206), timeout=300).content


def upload_to_youtube(v):
    meta = call(v["drive_account_id"], "GET", f"{DRIVE}/files/{v['drive_file_id']}",
                params={"fields": "size,name", "supportsAllDrives": "true"}).json()
    total = int(meta["size"])

    url, offset = v["upload_url"], 0
    if url:
        st = _session_offset(v, url, total)
        if isinstance(st, dict):
            return st["id"]
        if st < 0:
            url = None
        else:
            offset = st
    if not url:
        url, offset = _create_session(v, total), 0

    failures = 0
    while offset < total:
        end = min(offset + CHUNK, total) - 1
        try:
            data = _drive_chunk(v, offset, end)
            tok = access_token(v["channel_account_id"])
            r = requests.put(url, data=data, timeout=600, headers={
                "Authorization": "Bearer " + tok,
                "Content-Length": str(len(data)),
                "Content-Range": f"bytes {offset}-{offset + len(data) - 1}/{total}",
            })
            if r.status_code in (200, 201):
                return r.json()["id"]
            if r.status_code == 308:
                rng = r.headers.get("Range")
                offset = int(rng.split("-")[1]) + 1 if rng else 0
                failures = 0
                db.update("videos", v["id"], error=f"uploading {offset * 100 // total}%")
                continue
            if r.status_code in (400, 403, 404):
                raise GoogleError(r.status_code, r.text)
            raise IOError(f"YouTube answered {r.status_code}")
        except (IOError, requests.RequestException) as e:
            failures += 1
            if failures > 6:
                raise
            log.warning("chunk failed (%s), retrying", e)
            time.sleep(min(60, 2 ** failures))
            st = _session_offset(v, url, total)
            if isinstance(st, dict):
                return st["id"]
            if st < 0:
                raise IOError("upload session expired") from e
            offset = st
    st = _session_offset(v, url, total)
    if isinstance(st, dict):
        return st["id"]
    raise IOError("upload finished but YouTube did not confirm it")


def after_upload(v, video_id):
    notes = []
    if v["thumb_path"] and Path(v["thumb_path"]).exists():
        p = Path(v["thumb_path"])
        ctype = "image/png" if p.suffix.lower() == ".png" else "image/jpeg"
        try:
            call(v["channel_account_id"], "POST", YT_UPLOAD + "/thumbnails/set",
                 params={"videoId": video_id, "uploadType": "media"},
                 headers={"Content-Type": ctype}, data=p.read_bytes())
        except GoogleError as e:
            notes.append(f"thumbnail not set: {e}")
    if v["playlist_id"]:
        try:
            call(v["channel_account_id"], "POST", YT + "/playlistItems", params={"part": "snippet"},
                 json={"snippet": {"playlistId": v["playlist_id"],
                                   "resourceId": {"kind": "youtube#video", "videoId": video_id}}})
        except GoogleError as e:
            notes.append(f"playlist not set: {e}")
    try:
        call(v["drive_account_id"], "DELETE", f"{DRIVE}/files/{v['drive_file_id']}",
             params={"supportsAllDrives": "true"})
    except GoogleError as e:
        notes.append(f"Drive file not deleted: {e}")
    return "; ".join(notes) or None
