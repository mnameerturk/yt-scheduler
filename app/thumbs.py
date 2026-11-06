"""Flow thumbnails.

Life of a thumbnail job (videos.thumb_state):
  prompting  -> Claude writes 6 image prompts from the title/description (+ the channel's style note)
  queued     -> waiting for the Flow extension on this server to take it
  generating -> the extension is making the images in Google Flow
  ready      -> options saved; waiting for nmr to pick in the app
  picked     -> chosen (by nmr, or by Claude at the deadline); applied to the video
  failed     -> something went wrong (thumb_note says what)

If nmr hasn't picked by PICK_BEFORE before the post time, Claude looks at the options and
picks one. A pick made after the video is already posted is set on YouTube straight away.
"""
import io
import json
import logging
import os
import re
import shutil
import subprocess
import threading
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

from PIL import Image, ImageOps

from . import db
from .config import DATA_DIR

log = logging.getLogger("thumbs")

OPTIONS = int(os.getenv("THUMB_OPTIONS", "6"))
PICK_BEFORE = timedelta(hours=float(os.getenv("THUMB_PICK_HOURS", "2")))
FLOW_DIR = Path(os.getenv("FLOW_DOWNLOADS", str(Path.home() / "Downloads" / "FlowStorybooks")))
OPT_DIR = DATA_DIR / "thumbopts"
OPT_DIR.mkdir(parents=True, exist_ok=True)
STALE = timedelta(hours=3)
_wake = threading.Event()

COLS = {"thumb_state": "TEXT", "thumb_prompts": "TEXT", "thumb_options": "TEXT", "thumb_pick": "INTEGER",
        "thumb_by": "TEXT", "thumb_note": "TEXT", "thumb_job": "TEXT", "thumb_job_at": "TEXT",
        "thumb_deadline": "TEXT"}


def migrate():
    have = {r["name"] for r in db.q("PRAGMA table_info(videos)")}
    for c, t in COLS.items():
        if c not in have:
            db.x(f"ALTER TABLE videos ADD COLUMN {c} {t}")


migrate()


def wake():
    _wake.set()


def _utc(s):
    return datetime.fromisoformat(s.replace("Z", "+00:00")) if s else None


def deadline(v):
    """When Claude picks if nmr hasn't: 2 h before the post time (or a fixed time for already-posted videos)."""
    if v.get("thumb_deadline"):
        return _utc(v["thumb_deadline"])
    p = _utc(v.get("publish_at"))
    return (p - PICK_BEFORE) if p else None


# ---------- Claude ----------

def claude_bin():
    for c in (os.getenv("CLAUDE_BIN"), shutil.which("claude"), str(Path.home() / ".local/bin/claude")):
        if c and Path(c).exists():
            return c
    return None


def ask_claude(prompt, cwd, timeout=600):
    exe = claude_bin()
    if not exe:
        raise RuntimeError("Claude Code is not installed on the server")
    r = subprocess.run([exe, "-p", prompt, "--output-format", "json", "--allowedTools", "Read"],
                       cwd=str(cwd), capture_output=True, text=True, timeout=timeout)
    if r.returncode != 0:
        raise RuntimeError(f"Claude failed: {(r.stderr or r.stdout)[-300:]}")
    out = json.loads(r.stdout)
    if out.get("is_error"):
        raise RuntimeError(f"Claude error: {str(out.get('result'))[:300]}")
    return out.get("result", "")


def _json_in(text, opener):
    closer = "]" if opener == "[" else "}"
    i, j = text.find(opener), text.rfind(closer)
    if i < 0 or j < i:
        raise ValueError("no JSON in Claude's answer")
    return json.loads(text[i:j + 1])


def style_for(account_id):
    return db.kv_get(f"thumb_style:{account_id}", "") or ""


PROMPT_RULES = """You write image prompts for YouTube thumbnails. They are generated in Google Flow (Nano Banana) at 16:9.
Write {n} DIFFERENT thumbnail concepts for the video below, aimed at the highest click-through rate:
- one clear focal subject, big and close, readable on a small phone screen; strong contrast; a curiosity gap
- vary the concepts (different subjects, angles, emotions, compositions) so the creator gets real choice
- describe only visuals; no caption sentences, signs, labels, boards, price tags or UI
- if text truly helps, at most 1-3 huge bold words, spelled exactly, in quotes, and nothing else written anywhere
- each prompt 40-90 words, ends with: wide 16:9 composition
{style}
Video title: {title}
Channel: {channel}
Description: {desc}

Answer with ONLY a JSON array of {n} strings."""


def write_prompts(v):
    ch = db.q("SELECT channel_title FROM accounts WHERE id=?", (v["channel_account_id"],), one=True) or {}
    style = style_for(v["channel_account_id"])
    text = PROMPT_RULES.format(n=OPTIONS, title=v.get("title") or v.get("file_name") or "",
                               channel=ch.get("channel_title") or "", desc=(v.get("description") or "")[:1500],
                               style=f"- the channel's thumbnail style (follow it): {style}" if style else "")
    work = OPT_DIR / str(v["id"])
    work.mkdir(exist_ok=True)
    prompts = _json_in(ask_claude(text, work), "[")
    prompts = [re.sub(r"\s+", " ", str(p)).strip() for p in prompts if str(p).strip()][:OPTIONS]
    if len(prompts) < 2:
        raise ValueError("Claude returned too few prompts")
    return prompts


def fallback_prompts(v):
    t = v.get("title") or "this video"
    style = style_for(v["channel_account_id"])
    base = [f"Dramatic close-up of the single most striking subject from a video titled '{t}', strong rim light",
            f"Wide cinematic scene showing the central mystery of '{t}', one tiny human figure for scale, moody sky",
            f"Split composition contrasting before and after related to '{t}', bold color separation",
            f"Shocked expressive face reacting to the key reveal of '{t}', subject on the right, empty space on the left",
            f"Bold symbolic object representing '{t}' on a plain dark background, high contrast, glowing accents",
            f"Aerial view revealing the scale of '{t}', strong leading lines, vivid focal color"]
    return [f"{p}. {style} wide 16:9 composition".replace("  ", " ") for p in base][:OPTIONS]


def claude_pick(v, opts):
    work = OPT_DIR / str(v["id"])
    names = [Path(o).name for o in opts]
    text = (f"These files in the current folder are {len(names)} thumbnail options for a YouTube video titled "
            f"\"{v.get('title') or ''}\": {', '.join(names)}.\n"
            "Open and look at every one with the Read tool. Pick the one most likely to get the highest "
            "click-through rate on a phone: clear single subject, strong contrast, emotion/curiosity, no "
            "garbled text or broken anatomy, fits the title.\n"
            f"Answer with ONLY JSON: {{\"pick\": <1-{len(names)}>, \"why\": \"<one short sentence>\"}}")
    ans = _json_in(ask_claude(text, work), "{")
    n = int(ans["pick"])
    if not 1 <= n <= len(names):
        raise ValueError(f"pick {n} out of range")
    return n, str(ans.get("why", ""))[:200]


# ---------- jobs ----------

def start_job(vid):
    """(Re)make thumbnail options for a video. For a posted video, Claude picks after 12 h if nmr doesn't."""
    v = db.q("SELECT status FROM videos WHERE id=?", (vid,), one=True)
    dl = None
    if v and v["status"] == "posted":
        dl = (datetime.now(timezone.utc) + timedelta(hours=12)).replace(microsecond=0).isoformat()
    db.update("videos", vid, thumb_state="prompting", thumb_prompts=None, thumb_options=None, thumb_pick=None,
              thumb_by=None, thumb_note="writing prompts", thumb_job=None, thumb_job_at=None, thumb_deadline=dl)
    wake()


def book_text(v, job):
    prompts = json.loads(v["thumb_prompts"])
    lines = [f"BOOK: {job}", "MODEL: keep", "ASPECT: 16:9", "IMAGES: 1", "WAIT: 15-40",
             "BREAK EVERY: 8-10", "BREAK LENGTH: 60-120", "RETRIES: 3", ""]
    for k, p in enumerate(prompts, 1):
        lines += [f"PAGE t{k}:", p, ""]
    return "\n".join(lines)


def next_flow_job():
    now = datetime.now(timezone.utc)
    stale = (now - STALE).replace(microsecond=0).isoformat()
    db.x("UPDATE videos SET thumb_state='queued' WHERE thumb_state='generating' AND thumb_job_at < ?", (stale,))
    v = db.q("SELECT * FROM videos WHERE thumb_state='queued' ORDER BY COALESCE(publish_at, created_at) LIMIT 1",
             one=True)
    if not v:
        return None
    job = f"thumb_{v['id']}_{int(time.time())}"
    db.update("videos", v["id"], thumb_state="generating", thumb_job=job, thumb_job_at=db.now_iso(),
              thumb_note="making images in Flow")
    return {"job": job, "video_id": v["id"], "title": v.get("title"), "book": book_text(v, job)}


def _save_jpeg(src, dst):
    img = ImageOps.exif_transpose(Image.open(src)).convert("RGB")
    img = ImageOps.fit(img, (1280, 720), Image.Resampling.LANCZOS)
    for quality in (92, 85, 75, 65):
        buf = io.BytesIO()
        img.save(buf, "JPEG", quality=quality, optimize=True)
        if buf.tell() < 2 * 1024 * 1024:
            break
    Path(dst).write_bytes(buf.getvalue())


def flow_done(job):
    v = db.q("SELECT * FROM videos WHERE thumb_job=?", (job,), one=True)
    if not v:
        return {"ok": False, "error": "unknown job (video deleted?)"}
    src = FLOW_DIR / job
    files = sorted((f for f in src.glob("t*_v*.*") if re.match(r"t\d+_v\d+\.(jpe?g|png|webp)$", f.name, re.I)),
                   key=lambda f: [int(n) for n in re.findall(r"\d+", f.name)])
    if not files:
        db.update("videos", v["id"], thumb_state="failed", thumb_note=f"no images found in {src}")
        return {"ok": False, "error": "no images found"}
    out = OPT_DIR / str(v["id"])
    shutil.rmtree(out, ignore_errors=True)
    out.mkdir(parents=True)
    opts = []
    for k, f in enumerate(files, 1):
        dst = out / f"option{k}.jpg"
        _save_jpeg(f, dst)
        opts.append(str(dst))
    shutil.rmtree(src, ignore_errors=True)  # keep the server disk small
    dl = deadline(v)
    note = f"{len(opts)} options ready" + (f"; Claude picks at {dl.isoformat()} if you don't" if dl else "")
    db.update("videos", v["id"], thumb_state="ready", thumb_options=json.dumps(opts), thumb_note=note)
    _event("ready", v["id"], f"Thumbnails ready: {v.get('title') or v['file_name']}")
    wake()
    return {"ok": True, "options": len(opts)}


def flow_status(job, status, message=""):
    """The extension reports trouble (captcha / unusual activity / stuck prompt / daily limit)."""
    v = db.q("SELECT id, title, file_name FROM videos WHERE thumb_job=?", (job,), one=True) if job else None
    text = {"flagged by Google": "Flow: Google asks for a check (captcha/unusual activity). Open the screen.",
            "needs attention": "Flow is stuck on a prompt. Open the screen and press Resume.",
            "daily limit": "Flow daily limit reached; it continues automatically later."}.get(status, f"Flow: {status}")
    if v:
        db.update("videos", v["id"], thumb_note=f"{text} {message}".strip()[:300])
    db.kv_set("flow_alert", {"at": db.now_iso(), "status": status, "text": text, "message": message[:300]})
    if status != "daily limit":
        _event("alert", v["id"] if v else 0, text)


def _event(kind, vid, text):
    ev = db.kv_get("events", [])
    seq = (ev[-1]["seq"] + 1) if ev else 1
    ev.append({"seq": seq, "kind": kind, "video_id": vid, "text": text, "at": db.now_iso()})
    db.kv_set("events", ev[-50:])


def apply_pick(vid, n, by="you", why=""):
    from .transfer import set_youtube_thumbnail
    v = db.q("SELECT * FROM videos WHERE id=?", (vid,), one=True)
    opts = json.loads(v["thumb_options"] or "[]")
    if not 1 <= n <= len(opts):
        raise ValueError("no such option")
    path = DATA_DIR / "thumbs" / f"{vid}.jpg"
    shutil.copyfile(opts[n - 1], path)
    note = f"option {n} picked by {by}" + (f": {why}" if why else "")
    db.update("videos", vid, thumb_path=str(path), thumb_pick=n, thumb_by=by, thumb_state="picked", thumb_note=note)
    if v["status"] == "posted" and v["youtube_id"]:
        err = set_youtube_thumbnail(v, v["youtube_id"], path)
        db.update("videos", vid, thumb_note=note + ("; set on YouTube" if not err else f"; YouTube said: {err}"))
    if by == "Claude":
        _event("picked", vid, f"Claude picked thumbnail {n} for {v.get('title') or v['file_name']}")


# ---------- worker ----------

def _tick():
    for v in db.q("SELECT * FROM videos WHERE thumb_state='prompting' ORDER BY id"):
        try:
            prompts, note = write_prompts(v), "prompts written by Claude"
        except Exception as e:  # noqa: BLE001
            log.warning("Claude could not write prompts for %s: %s", v["id"], e)
            prompts, note = fallback_prompts(v), f"basic prompts used (Claude: {str(e)[:150]})"
        db.update("videos", v["id"], thumb_prompts=json.dumps(prompts), thumb_state="queued", thumb_note=note)

    now = datetime.now(timezone.utc)
    for v in db.q("SELECT * FROM videos WHERE thumb_state='ready'"):
        dl = deadline(v)
        if not dl or now < dl:
            continue
        opts = json.loads(v["thumb_options"] or "[]")
        try:
            n, why = claude_pick(v, opts)
        except Exception as e:  # noqa: BLE001
            log.warning("Claude could not pick for %s: %s", v["id"], e)
            n, why = 1, f"Claude unavailable, used option 1 ({str(e)[:100]})"
        apply_pick(v["id"], n, by="Claude", why=why)


def loop():
    while True:
        try:
            _tick()
        except Exception:  # noqa: BLE001
            log.exception("thumbnail worker error")
        _wake.wait(30)
        _wake.clear()


def start():
    threading.Thread(target=loop, daemon=True, name="thumbs").start()
