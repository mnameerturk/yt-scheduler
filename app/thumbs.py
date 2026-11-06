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
MORE = int(os.getenv("MORE_COUNT", "3"))   # extra titles / thumbnails per "more" tap
PICK_BEFORE = timedelta(hours=float(os.getenv("THUMB_PICK_HOURS", "2")))
FLOW_DIR = Path(os.getenv("FLOW_DOWNLOADS", str(Path.home() / "Downloads" / "FlowStorybooks")))
OPT_DIR = DATA_DIR / "thumbopts"
REFS_DIR = DATA_DIR / "refs"   # refs/<channel_slug>/  ref*.jpg + style.txt (best-performing examples)
OPT_DIR.mkdir(parents=True, exist_ok=True)
SCRIPT_DIR = DATA_DIR / "scripts"
SCRIPT_DIR.mkdir(parents=True, exist_ok=True)
SUMMARY_MODEL = os.getenv("SUMMARY_MODEL", "haiku")
WRITER_MODEL = os.getenv("WRITER_MODEL", "opus")
WRITER_EFFORT = os.getenv("WRITER_EFFORT", "medium")
STALE = timedelta(hours=3)
_wake = threading.Event()

COLS = {"thumb_state": "TEXT", "thumb_prompts": "TEXT", "thumb_options": "TEXT", "thumb_pick": "INTEGER",
        "thumb_by": "TEXT", "thumb_note": "TEXT", "thumb_job": "TEXT", "thumb_job_at": "TEXT",
        "thumb_deadline": "TEXT", "meta_state": "TEXT", "meta_titles": "TEXT", "meta_note": "TEXT",
        "script_path": "TEXT", "thumb_append": "INTEGER"}


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


def ask_claude(prompt, cwd, timeout=900, model=None, effort=None, stdin=None):
    exe = claude_bin()
    if not exe:
        raise RuntimeError("Claude Code is not installed on the server")
    cmd = [exe, "-p", prompt, "--output-format", "json", "--allowedTools", "Read"]
    if model:
        cmd += ["--model", model]
    if effort:
        cmd += ["--effort", effort]
    r = subprocess.run(cmd, cwd=str(cwd), input=stdin if stdin is not None else "", capture_output=True,
                       text=True, timeout=timeout)
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


def _slug(t):
    return re.sub(r"[^a-z0-9]+", "_", (t or "").lower()).strip("_")


def refs_dir(account_id):
    """The channel's reference folder (matched by channel name), or None."""
    ch = db.q("SELECT channel_title FROM accounts WHERE id=?", (account_id,), one=True) or {}
    want = _slug(ch.get("channel_title"))
    if not want or not REFS_DIR.exists():
        return None
    for d in REFS_DIR.iterdir():
        if d.is_dir() and (d.name == want or d.name in want or want in d.name):
            return d
    return None


def copy_refs(account_id, work):
    """Put the channel's reference thumbnails next to the work files (Claude reads them there)."""
    out = []
    d = refs_dir(account_id)
    if d:
        for k, f in enumerate(sorted(d.glob("*.jp*g")) + sorted(d.glob("*.png")), 1):
            dst = Path(work) / f"reference{k}{f.suffix.lower()}"
            shutil.copyfile(f, dst)
            out.append(dst.name)
    return out


def style_for(account_id):
    """Style written in the app (if any) + the channel's style.txt from its reference folder."""
    parts = []
    d = refs_dir(account_id)
    if d and (d / "style.txt").exists():
        parts.append((d / "style.txt").read_text(encoding="utf-8").strip())
    note = db.kv_get(f"thumb_style:{account_id}", "") or ""
    if note.strip():
        parts.append("Extra notes from the creator: " + note.strip())
    return "\n".join(parts)


def app_style(account_id):
    return db.kv_get(f"thumb_style:{account_id}", "") or ""


PROMPT_RULES = """You write image prompts for YouTube thumbnails. Each prompt is generated in Google Flow (Nano Banana) at 16:9
and the image is used AS IS: it must be the complete, finished thumbnail. Nothing gets edited afterwards.
{refs}
Write {n} DIFFERENT thumbnail prompts for the video below, aimed at the highest click-through rate:
- THE SUBJECT RULE (most important): every thumbnail must show what THIS video is actually about: the exact
  animal/object/person/place of this story (e.g. a video about a grizzly shows that grizzly in every option). The
  reference thumbnails are only for STYLE; never take their subjects (their tiger, elephant, lion, sword…) unless
  this video is about that same thing. Any second element (object, person, prey, place) must come from this story.
- copy the channel's proven style closely (layout, text treatment, colours, arrows, background, lighting)
- include EVERYTHING in the prompt: the exact overlay text in double quotes (2-4 words, spelled exactly), its font look,
  colours (which word is red), outline/shadow, banner, size and position; every arrow (colour, style, where it points);
  any cut-out objects; the background. Say "no other text anywhere in the image"
- one clear focal subject, big and readable on a small phone screen; strong contrast; a curiosity gap that teases the title
  without repeating it; the hook text must tease a real moment or fact from THIS story, not a generic line
- vary the {n} concepts WITHIN this story: different hook text, moment of the story, pose/expression, angle,
  secondary story element or composition, so the creator gets real choice. Never swap in a different animal/object
- never include a video duration timestamp, channel logo, watermark or YouTube UI
- each prompt 70-140 words, ending with: wide 16:9 YouTube thumbnail
{style}
Video title: {title}
Channel: {channel}
Description: {desc}

Answer with ONLY a JSON array of {n} strings."""


def _summary(v):
    f = OPT_DIR / str(v["id"]) / "summary.txt"
    return f.read_text(encoding="utf-8") if f.exists() else ""


def write_prompts(v, n=OPTIONS, avoid=()):
    ch = db.q("SELECT channel_title FROM accounts WHERE id=?", (v["channel_account_id"],), one=True) or {}
    style = style_for(v["channel_account_id"])
    work = OPT_DIR / str(v["id"])
    work.mkdir(exist_ok=True)
    refs = copy_refs(v["channel_account_id"], work)
    ref_text = (f"FIRST open and study every one of these best-performing thumbnails from this channel with the Read tool: "
                f"{', '.join(refs)}. Ignore the small duration timestamp in their corner.") if refs else ""
    desc = (v.get("description") or "")[:1500]
    summ = _summary(v)
    if summ:
        desc += "\n\nBriefing on the full script:\n" + summ[:8000]
    text = PROMPT_RULES.format(n=n, title=v.get("title") or v.get("file_name") or "",
                               channel=ch.get("channel_title") or "", desc=desc,
                               refs=ref_text, style=f"Channel thumbnail style guide:\n{style}" if style else "")
    if avoid:
        text += ("\n\nThese concepts were already made; write COMPLETELY DIFFERENT ones (new hook text, "
                 "story moment, pose and composition), still showing THIS video's own subject:\n- " + "\n- ".join(a[:300] for a in avoid))
    prompts = _json_in(ask_claude(text, work, model=WRITER_MODEL, effort=WRITER_EFFORT), "[")
    prompts = [re.sub(r"\s+", " ", str(p)).strip() for p in prompts if str(p).strip()][:n]
    if len(prompts) < min(2, n):
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
    refs = copy_refs(v["channel_account_id"], work)
    ref_text = (f"For comparison, {', '.join(refs)} are this channel's best-performing thumbnails; prefer the option "
                "that matches their style. ") if refs else ""
    text = (f"These files in the current folder are {len(names)} thumbnail options for a YouTube video titled "
            f"\"{v.get('title') or ''}\": {', '.join(names)}.\n"
            "Open and look at every one with the Read tool. " + ref_text +
            "Pick the one most likely to get the highest click-through rate on a phone: clear single subject, "
            "strong contrast, emotion/curiosity, overlay text spelled correctly and readable (reject any option "
            "with misspelled, garbled or extra text, or broken anatomy), fits the title.\n"
            f"Answer with ONLY JSON: {{\"pick\": <1-{len(names)}>, \"why\": \"<one short sentence>\"}}")
    ans = _json_in(ask_claude(text, work, model=WRITER_MODEL, effort=WRITER_EFFORT), "{")
    n = int(ans["pick"])
    if not 1 <= n <= len(names):
        raise ValueError(f"pick {n} out of range")
    return n, str(ans.get("why", ""))[:200]


# ---------- titles + description from the script ----------

SUMMARY_PROMPT = """The full narration script of a long YouTube documentary is piped in. Write a dense briefing for the
person who will write its title, description and thumbnail. Plain text, 500-900 words:
1. The topic in one line.
2. Every part/item the video covers, in order, each with its single most surprising fact.
3. The 5 most shocking or curiosity-provoking moments (what would make someone click).
4. Key names, places, dates and numbers.
Do not invent anything that is not in the script."""

META_PROMPT = """You package YouTube videos for the channel "{channel}" for the highest click-through rate.
{refs}
Below is a briefing on the video's script (written from the full script).

Write:
1. "titles": 3 title options, best first. Max 70 characters each. Match the channel's proven title style shown in
   the style guide. Curiosity gap, specific, no clickbait that the video doesn't pay off.
2. "description": 120-250 words. First two lines hook the viewer and naturally contain the main search keywords.
   Then a short overview of what the video covers (no timestamps), then 3 relevant hashtags on the last line.
   Plain text, no markdown, no links.
3. "thumbnails": {n} thumbnail image prompts for Google Flow (Nano Banana, 16:9) made to pair with title option 1.
   Each image is used AS IS as the finished thumbnail, nothing is edited afterwards, so include EVERYTHING: the exact
   overlay text in double quotes (2-4 words, spelled exactly, it must not just repeat the title), font look, colours
   (which word is red), outline/shadow, banner, size and position; every arrow (colour, style, where it points); any
   cut-out objects; the background; and say "no other text anywhere in the image". Copy the channel's proven
   thumbnail style closely.
   THE SUBJECT RULE (most important): every thumbnail must show what THIS video is actually about: the exact
   animal/object/person/place of this story (e.g. a video about a grizzly shows that grizzly in every option). The
   reference thumbnails are only for STYLE; never take their subjects (their tiger, elephant, lion, sword…) unless
   this video is about that same thing. Any second element (object, person, prey, place) must come from this story.
   Vary the {n} concepts WITHIN this story (hook text, story moment, pose, angle, secondary story element), never by
   swapping in a different animal/object. One clear focal subject readable on a phone.
   Never a duration timestamp, logo, watermark or YouTube UI. 70-140 words each, ending with: wide 16:9 YouTube thumbnail

Channel style guide:
{style}

Briefing:
{summary}

Answer with ONLY JSON: {{"titles": [...], "description": "...", "thumbnails": [...]}}"""


def start_meta(vid, script):
    path = SCRIPT_DIR / f"{vid}.txt"
    path.write_text(script, encoding="utf-8")
    db.update("videos", vid, script_path=str(path), meta_state="summarizing", meta_titles=None,
              meta_note="Haiku is reading the script")
    wake()


def desc_footer(account_id):
    return (db.kv_get(f"desc_footer:{account_id}", "") or "").strip()


def write_meta(v):
    ch = db.q("SELECT channel_title FROM accounts WHERE id=?", (v["channel_account_id"],), one=True) or {}
    work = OPT_DIR / str(v["id"])
    work.mkdir(exist_ok=True)
    script = Path(v["script_path"]).read_text(encoding="utf-8")
    summary = ask_claude(SUMMARY_PROMPT, work, model=SUMMARY_MODEL, stdin=script, timeout=1200).strip()
    (work / "summary.txt").write_text(summary, encoding="utf-8")
    db.update("videos", v["id"], meta_state="writing", meta_note="Opus is writing titles, description and thumbnails")
    refs = copy_refs(v["channel_account_id"], work)
    ref_text = (f"FIRST open and study every one of these best-performing thumbnails from this channel with the Read "
                f"tool: {', '.join(refs)}. Ignore the small duration timestamp in their corner.") if refs else ""
    text = META_PROMPT.format(channel=ch.get("channel_title") or "", refs=ref_text, n=OPTIONS,
                              style=style_for(v["channel_account_id"]) or "(none yet)", summary=summary[:12000])
    out = _json_in(ask_claude(text, work, model=WRITER_MODEL, effort=WRITER_EFFORT), "{")
    titles = [str(t).strip()[:100] for t in out.get("titles", []) if str(t).strip()][:3]
    thumbs_ = [re.sub(r"\s+", " ", str(p)).strip() for p in out.get("thumbnails", []) if str(p).strip()][:OPTIONS]
    desc = str(out.get("description", "")).strip()
    if not titles or not desc:
        raise ValueError("Opus returned no titles/description")
    return titles, desc, thumbs_


MORE_TITLES_PROMPT = """You write YouTube titles for the channel "{channel}" for the highest click-through rate.
Write {n} NEW title options for the video below. Max 70 characters each. Match the channel's proven title style.
Curiosity gap, specific, no clickbait the video doesn't pay off. They must be clearly different from these
existing options: {existing}

Channel style guide:
{style}

Current title: {title}
Description: {desc}
{summary}
Answer with ONLY a JSON array of {n} strings."""


def more_titles(v):
    ch = db.q("SELECT channel_title FROM accounts WHERE id=?", (v["channel_account_id"],), one=True) or {}
    work = OPT_DIR / str(v["id"])
    work.mkdir(exist_ok=True)
    existing = json.loads(v["meta_titles"] or "[]")
    if v.get("title") and v["title"] not in existing:
        existing.append(v["title"])
    summ = _summary(v)
    text = MORE_TITLES_PROMPT.format(channel=ch.get("channel_title") or "", n=MORE, existing=json.dumps(existing),
                                     style=style_for(v["channel_account_id"]) or "(none yet)", title=v.get("title") or "",
                                     desc=(v.get("description") or "")[:1500],
                                     summary=("Briefing on the full script:\n" + summ[:8000]) if summ else "")
    new = [str(t).strip()[:100] for t in _json_in(ask_claude(text, work, model=WRITER_MODEL, effort=WRITER_EFFORT), "[")]
    return existing + [t for t in new if t and t not in existing][:MORE]


def _meta_tick():
    for v in db.q("SELECT * FROM videos WHERE meta_state='more' ORDER BY id"):
        try:
            titles = more_titles(v)
            db.update("videos", v["id"], meta_state="ready", meta_titles=json.dumps(titles),
                      meta_note=f"{len(titles)} title options")
            _event("meta", v["id"], f"More titles ready: {v.get('title') or v['file_name']}")
        except Exception as e:  # noqa: BLE001
            log.warning("more titles failed for %s: %s", v["id"], e)
            db.update("videos", v["id"], meta_state="ready" if v["meta_titles"] else "failed",
                      meta_note=f"Claude could not write more titles: {str(e)[:200]}")
    for v in db.q("SELECT * FROM videos WHERE meta_state IN ('summarizing','writing') ORDER BY id"):
        try:
            titles, desc, prompts = write_meta(v)
        except Exception as e:  # noqa: BLE001
            log.warning("title/description failed for %s: %s", v["id"], e)
            db.update("videos", v["id"], meta_state="failed", meta_note=f"Claude could not write them: {str(e)[:200]}")
            continue
        fields = {"meta_state": "ready", "meta_titles": json.dumps(titles),
                  "meta_note": "title option 1 is set; tap another to switch"}
        fresh = db.q("SELECT * FROM videos WHERE id=?", (v["id"],), one=True)
        if fresh["status"] not in ("posting", "posted"):
            placeholder = (fresh["title"] or "").strip() in ("", Path(fresh["file_name"] or "").stem)
            if placeholder:   # never overwrite a title you typed yourself
                fields["title"] = titles[0]
            else:
                fields["meta_note"] = "your title is kept; tap an option to use it instead"
            if not (fresh["description"] or "").strip():
                footer = desc_footer(v["channel_account_id"])
                fields["description"] = desc + ("\n\n" + footer if footer else "")
        if fresh.get("thumb_state") == "prompting" and len(prompts) >= 2:
            fields.update(thumb_prompts=json.dumps(prompts), thumb_state="queued",
                          thumb_note="prompts written by Opus from the script")
        db.update("videos", v["id"], **fields)
        _event("meta", v["id"], f"Title ready: {titles[0]}")


# ---------- jobs ----------

def start_more_titles(vid):
    db.update("videos", vid, meta_state="more", meta_note="Opus is writing more titles")
    wake()


def start_more_thumbs(vid):
    v = db.q("SELECT thumb_options FROM videos WHERE id=?", (vid,), one=True)
    have = len(json.loads(v["thumb_options"] or "[]"))
    if not have:
        return start_job(vid)
    db.update("videos", vid, thumb_state="prompting", thumb_append=have, thumb_note=f"writing {MORE} more prompts")
    wake()


def start_job(vid):
    """(Re)make thumbnail options for a video. For a posted video, Claude picks after 12 h if nmr doesn't."""
    v = db.q("SELECT status FROM videos WHERE id=?", (vid,), one=True)
    dl = None
    if v and v["status"] == "posted":
        dl = (datetime.now(timezone.utc) + timedelta(hours=12)).replace(microsecond=0).isoformat()
    db.update("videos", vid, thumb_state="prompting", thumb_prompts=None, thumb_options=None, thumb_pick=None,
              thumb_by=None, thumb_note="writing prompts", thumb_job=None, thumb_job_at=None, thumb_deadline=dl,
              thumb_append=None)
    wake()


def book_text(v, job):
    prompts = json.loads(v["thumb_prompts"])[v.get("thumb_append") or 0:]
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
    more = v.get("thumb_append") is not None
    opts = json.loads(v["thumb_options"] or "[]") if more else []
    if not more:
        for old in out.glob("option*.jpg"):
            old.unlink(missing_ok=True)
    out.mkdir(parents=True, exist_ok=True)
    first = len(opts) + 1
    for k, f in enumerate(files, first):
        dst = out / f"option{k}.jpg"
        _save_jpeg(f, dst)
        opts.append(str(dst))
    shutil.rmtree(src, ignore_errors=True)  # keep the server disk small
    dl = deadline(v)
    note = f"{len(opts)} options ready" + (f"; Claude picks at {dl.isoformat()} if you don't" if dl and not v["thumb_pick"] else "")
    # with "more", an earlier pick stays in use until you tap a new one
    state = "picked" if (more and v["thumb_pick"]) else "ready"
    db.update("videos", v["id"], thumb_state=state, thumb_options=json.dumps(opts), thumb_note=note, thumb_append=None)
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
    busy = v["thumb_state"] in ("prompting", "queued", "generating")   # "more" still running: keep that going
    db.update("videos", vid, thumb_path=str(path), thumb_pick=n, thumb_by=by,
              thumb_state=v["thumb_state"] if busy else "picked", thumb_note=v["thumb_note"] if busy else note)
    if v["status"] == "posted" and v["youtube_id"]:
        err = set_youtube_thumbnail(v, v["youtube_id"], path)
        db.update("videos", vid, thumb_note=note + ("; set on YouTube" if not err else f"; YouTube said: {err}"))
    if by == "Claude":
        _event("picked", vid, f"Claude picked thumbnail {n} for {v.get('title') or v['file_name']}")


# ---------- worker ----------

def _tick():
    _meta_tick()
    # thumbnails for videos without a script (with a script, the meta step writes the prompts)
    for v in db.q("SELECT * FROM videos WHERE thumb_state='prompting' AND (meta_state IS NULL OR meta_state "
                  "IN ('failed','ready')) ORDER BY id"):
        more = v.get("thumb_append") is not None
        old = json.loads(v["thumb_prompts"] or "[]") if more else []
        try:
            prompts = write_prompts(v, n=MORE, avoid=old) if more else write_prompts(v)
            note = f"{MORE} more prompts written by Claude" if more else "prompts written by Claude"
        except Exception as e:  # noqa: BLE001
            log.warning("Claude could not write prompts for %s: %s", v["id"], e)
            prompts = fallback_prompts(v)
            prompts, note = (prompts[:MORE] if more else prompts), f"basic prompts used (Claude: {str(e)[:150]})"
        if more:
            # the new prompts go after the ones already made; the Flow job only runs the new ones
            db.update("videos", v["id"], thumb_append=len(old))
        db.update("videos", v["id"], thumb_prompts=json.dumps(old + prompts), thumb_state="queued", thumb_note=note)

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
