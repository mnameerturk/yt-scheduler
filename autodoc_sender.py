"""AutoDoc -> NMR Scheduler, automatically.

Watches AutoDoc's projects folder. When a project finishes rendering, it uploads the video to Drive once,
adds it to the scheduler queue on the right channel (by the project's profile) and sends the script, so
Claude writes the titles, description and thumbnails on the server.

  pythonw autodoc_sender.py                 watch (install_autostart.bat starts this with Windows)
  python  autodoc_sender.py --list          finished projects and whether they were sent
  python  autodoc_sender.py --send NAME...  send specific (older) projects now, e.g. 08_old_ephraim_utah_1923

Early start: as soon as a new project's voiceover is done (its script is final), the video is added to the
scheduler as "being made in AutoDoc" with the script, so Claude writes the titles/description and Flow makes
the thumbnails while AutoDoc renders. When the render finishes, the file is uploaded and attached to it.

Flow Agent thumbnails: after you tap "Copy for Flow Agent" in the app, image downloads that look like a
thumbnail (16:9) in your Downloads folder during the next 3 hours are added to that video as options.

Which file is sent: final.mp4 - AutoDoc's finished render, with the channel's background music mixed in
and no captions (captions are off). A final_captioned.mp4 is only used if "use_captioned" is true in
autodoc_sender.json. Settings: autodoc_sender.json
"""
import argparse
import json
import socket
import sys
import time
import traceback
from datetime import datetime
from pathlib import Path

import send_to_queue as stq

HERE = Path(__file__).resolve().parent
CONF = HERE / "autodoc_sender.json"
LOG = HERE / "autodoc_sender.log"
MARK = ".sent_to_scheduler.json"
EARLY = ".scheduler_entry.json"   # created before the render finished
DEFAULTS = {
    "projects_dir": r"D:\New folder (4)\autodoc_projects",
    "channels": {"wildlife": "Lost Archive", "ancient_history": "Known"},  # AutoDoc profile -> part of channel name
    "wait_minutes": 5,     # let the finished file settle before sending
    "check_every_seconds": 120,
    "downloads_dir": str(Path.home() / "Downloads"),
    "watch_downloads": False,   # the Flow extension sends Agent images itself; turn on if you don't use it
}
INBOX_STATE = HERE / "autodoc_sender_inbox.json"
IMG_EXT = {".png", ".jpg", ".jpeg", ".webp"}


def log(msg):
    line = f"{datetime.now():%Y-%m-%d %H:%M:%S}  {msg}"
    print(line, flush=True)
    try:
        if LOG.exists() and LOG.stat().st_size > 2_000_000:
            LOG.replace(LOG.with_suffix(".old.log"))
        with LOG.open("a", encoding="utf-8") as f:
            f.write(line + "\n")
    except OSError:
        pass


def conf():
    c = dict(DEFAULTS)
    if CONF.exists():
        c.update(json.loads(CONF.read_text(encoding="utf-8")))
    if "since" not in c:  # first run: only renders finished from now on are sent automatically
        c["since"] = time.time()
        CONF.write_text(json.dumps(c, indent=2), encoding="utf-8")
    return c


def finished(p):
    """Render finished time (epoch) or None."""
    try:
        q = json.loads((p / "queue_progress.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if q.get("phase") != "done" or not (p / "final.mp4").exists():
        return None
    return float(q.get("ts") or (p / "final.mp4").stat().st_mtime)


def video_file(p):
    cap = p / "final_captioned.mp4"
    if conf().get("use_captioned") and cap.exists():
        return cap
    return p / "final.mp4"


def profile(p):
    try:
        return json.loads((p / "project.json").read_text(encoding="utf-8")).get("profile", "")
    except (OSError, ValueError):
        return ""


def channel_for(srv, p, c):
    prof = profile(p)
    want = c["channels"].get(prof)
    if not want:
        raise RuntimeError(f"no channel set for AutoDoc profile '{prof}' (add it to autodoc_sender.json)")
    chans = [ch for ch in srv.req("GET", "/api/channels") if ch["channel_id"]]
    hits = [ch for ch in chans if want.lower() in (ch["channel_title"] or "").lower()]
    if len(hits) != 1:
        raise RuntimeError(f"'{want}' matched {len(hits)} channels")
    return hits[0]


def script_of(p):
    try:
        return json.loads((p / "project.json").read_text(encoding="utf-8")).get("script", "") or ""
    except (OSError, ValueError):
        return ""


def created(p):
    try:
        return float(json.loads((p / "project.json").read_text(encoding="utf-8")).get("created") or 0)
    except (OSError, ValueError):
        return 0.0


def register_early(srv, p, c):
    """Voiceover done -> add to the scheduler now (no file yet) so titles + thumbnails are ready early."""
    channel = channel_for(srv, p, c)
    script = script_of(p)
    v = srv.req("POST", "/api/videos", json={
        "channel_account_id": channel["id"], "rendering": True, "file_name": f"{p.name}.mp4", "title": p.name,
        "description": "", "tags": "", "auto_thumbnail": True, "script": script})
    (p / EARLY).write_text(json.dumps({"video_id": v["id"], "channel": channel["channel_title"],
                                       "at": datetime.now().isoformat(timespec="seconds")}), encoding="utf-8")
    log(f"{p.name}: voiceover done -> added as #{v['id']} (being made). Claude + Flow start on titles/thumbnails now.")


def send(srv, p, c):
    early = json.loads((p / EARLY).read_text(encoding="utf-8")) if (p / EARLY).exists() else None
    if early:
        try:
            if srv.req("GET", f"/api/videos/{early['video_id']}").get("status") != "rendering":
                early = None   # already has a file somehow -> send as a new video
        except RuntimeError as e:
            if "404" not in str(e):
                raise
            early = None   # deleted in the app meanwhile -> send as a new video
    prof = profile(p)
    want = c["channels"].get(prof)
    if not want:
        raise RuntimeError(f"no channel set for AutoDoc profile '{prof}' (add it to autodoc_sender.json)")
    chans = [ch for ch in srv.req("GET", "/api/channels") if ch["channel_id"]]
    hits = [ch for ch in chans if want.lower() in (ch["channel_title"] or "").lower()]
    if len(hits) != 1:
        raise RuntimeError(f"'{want}' matched {len(hits)} channels")
    channel = hits[0]
    vf = video_file(p)
    log(f"{p.name}: sending {vf.name} ({vf.stat().st_size / 1024 / 1024:.0f} MB) to {channel['channel_title']}")
    try:
        file_id, drive_acc = stq.upload_to_drive(srv, channel, vf)
    except SystemExit as e:  # the uploader exits on hard failures; here we just retry later
        raise RuntimeError(f"Drive upload stopped: {e}") from None
    if early:
        v = srv.req("POST", f"/api/videos/{early['video_id']}/attach-file", json={
            "drive_account_id": drive_acc, "drive_file_id": file_id, "file_size": vf.stat().st_size})
        (p / MARK).write_text(json.dumps({"video_id": v["id"], "file": vf.name, "channel": channel["channel_title"],
                                          "at": datetime.now().isoformat(timespec="seconds")}), encoding="utf-8")
        log(f"{p.name}: file attached to #{v['id']}; it's ready to post.")
        return v
    script = stq.find_script(vf)
    v = srv.req("POST", "/api/videos", json={
        "channel_account_id": channel["id"], "drive_account_id": drive_acc, "drive_file_id": file_id,
        "file_name": f"{p.name}.mp4", "file_size": vf.stat().st_size, "title": p.name,
        "description": "", "tags": "", "auto_thumbnail": True, "script": script})
    (p / MARK).write_text(json.dumps({"video_id": v["id"], "file": vf.name, "channel": channel["channel_title"],
                                      "at": datetime.now().isoformat(timespec="seconds")}), encoding="utf-8")
    log(f"{p.name}: queued as #{v['id']} (script {len(script.split())} words). Claude writes title/description/thumbnails.")
    return v


def scan(srv, c):
    root = Path(c["projects_dir"])
    now = time.time()
    for p in sorted(root.iterdir()):
        if not p.is_dir() or (p / MARK).exists():
            continue
        if (not (p / EARLY).exists() and (p / ".voice_done").exists() and created(p) >= c["since"]
                and script_of(p).strip() and not finished(p)):
            try:
                register_early(srv, p, c)
            except (Exception, SystemExit) as e:  # noqa: BLE001
                log(f"{p.name}: not added early ({e}); will retry")
        done = finished(p)
        if not done or done < c["since"]:
            continue
        vf = video_file(p)
        if now - max(done, vf.stat().st_mtime) < c["wait_minutes"] * 60:
            continue  # give time to add captions / let the file settle
        try:
            send(srv, p, c)
        except (Exception, SystemExit) as e:  # noqa: BLE001  (server/Drive unreachable -> next round)
            log(f"{p.name}: not sent yet ({e}); will retry")


def watch_downloads(srv, c):
    """Images you download from Flow while a video is waiting for them -> options for that video."""
    ib = srv.req("GET", "/api/inbox")
    if not ib.get("video_id"):
        return
    try:
        from PIL import Image
    except ImportError:
        Image = None
    seen = json.loads(INBOX_STATE.read_text(encoding="utf-8")) if INBOX_STATE.exists() else {}
    folder = Path(c["downloads_dir"])
    now = time.time()
    for f in sorted(folder.glob("*"), key=lambda x: x.stat().st_mtime if x.exists() else 0):
        try:
            st = f.stat()
        except OSError:
            continue
        key = f"{f.name}|{int(st.st_mtime)}|{st.st_size}"
        if (f.suffix.lower() not in IMG_EXT or key in seen or st.st_mtime < ib["since"]
                or now - st.st_mtime < 5 or st.st_size < 40_000):
            continue
        if Image:
            try:
                with Image.open(f) as im:
                    ratio = im.width / im.height
                if not 1.55 <= ratio <= 1.95:      # not a 16:9 thumbnail: leave it alone
                    seen[key] = "skipped"
                    continue
            except Exception:  # noqa: BLE001
                continue
        with f.open("rb") as fh:
            r = srv.req("POST", f"/api/videos/{ib['video_id']}/add-option", files={"file": (f.name, fh)})
        seen[key] = ib["video_id"]
        log(f"Flow Agent image {f.name} -> video #{ib['video_id']} (option {r.get('options')})")
    INBOX_STATE.write_text(json.dumps(dict(list(seen.items())[-500:])), encoding="utf-8")


def single_instance():
    s = socket.socket()
    try:
        s.bind(("127.0.0.1", 47831))
    except OSError:
        sys.exit("autodoc_sender is already running.")
    return s


def main():
    a = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    a.add_argument("--list", action="store_true")
    a.add_argument("--send", nargs="+", metavar="PROJECT")
    args = a.parse_args()
    c = conf()
    root = Path(c["projects_dir"])
    if args.list:
        for p in sorted(root.iterdir()):
            if p.is_dir() and finished(p):
                m = p / MARK
                st = f"sent #{json.loads(m.read_text())['video_id']}" if m.exists() else "not sent"
                print(f"{st:<12} {profile(p):<16} {video_file(p).name:<20} {p.name}")
        return
    srv = stq.Server(stq.load_conf())
    if args.send:
        for name in args.send:
            p = root / name
            if not finished(p):
                print(f"{name}: not a finished AutoDoc project, skipped")
                continue
            if (p / MARK).exists():
                print(f"{name}: already sent (delete {MARK} in that folder to send again)")
                continue
            send(srv, p, c)
        return
    lock = single_instance()  # noqa: F841
    log(f"watching {root} (new renders wait {c['wait_minutes']} min before sending)")
    last_scan = 0
    while True:
        try:
            if c.get("watch_downloads"):
                watch_downloads(srv, c)           # every 15 s: Flow Agent downloads
        except (Exception, SystemExit):  # noqa: BLE001
            log("downloads watch error: " + traceback.format_exc(limit=1).replace("\n", " | "))
        if time.time() - last_scan >= c["check_every_seconds"]:
            last_scan = time.time()
            try:
                scan(srv, c)
            except (Exception, SystemExit):  # noqa: BLE001
                log("scan error: " + traceback.format_exc(limit=2).replace("\n", " | "))
        time.sleep(15)


if __name__ == "__main__":
    main()
