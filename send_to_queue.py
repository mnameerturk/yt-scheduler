"""PC side: upload a finished video to Google Drive once and add it to the scheduler queue.

Examples
  python send_to_queue.py --setup
  python send_to_queue.py --status
  python send_to_queue.py "D:\\path\\final.mp4" --channel "Known" --title "My title" ^
      --description-file desc.txt --tags "history,ancient" --time "2026-11-02 18:00" --thumbnail thumb.jpg

If the connection drops, run the same command again: the upload continues where it stopped.
"""
import argparse
import json
import sys
import time
from pathlib import Path

import requests

HERE = Path(__file__).resolve().parent
CONF = HERE / "yt_scheduler_pc.json"
DRIVE_UPLOAD = "https://www.googleapis.com/upload/drive/v3/files"
CHUNK = 16 * 1024 * 1024


def load_conf():
    if not CONF.exists():
        sys.exit("Not set up yet. Run:  python send_to_queue.py --setup")
    return json.loads(CONF.read_text())


class Server:
    def __init__(self, conf):
        self.base = conf["server"].rstrip("/")
        self.h = {"Authorization": "Bearer " + conf["password"], "ngrok-skip-browser-warning": "1"}

    def req(self, method, path, **kw):
        for attempt in range(5):
            try:
                if "files" in kw:  # rewind file uploads before a retry
                    for f in kw["files"].values():
                        f.seek(0)
                r = requests.request(method, self.base + path, headers=self.h, timeout=120, **kw)
                break
            except requests.RequestException as e:
                if attempt == 4:
                    sys.exit(f"Can't reach the server ({e.__class__.__name__}). "
                             "Check that run_windows.bat and the ngrok window are running, then try again.")
                print("Connection hiccup, retrying...")
                time.sleep(3 * (attempt + 1))
        if r.status_code == 401:
            sys.exit("Server says the app password is wrong. Run --setup again.")
        if not r.ok:
            raise RuntimeError(f"server {r.status_code}: {r.text[:300]}")
        return r.json()


def pick_channel(srv, name):
    chans = [c for c in srv.req("GET", "/api/channels") if c["channel_id"]]
    if not chans:
        sys.exit("No channels connected. Open the scheduler page and add one first.")
    if not name:
        if len(chans) == 1:
            return chans[0]
        sys.exit("Choose a channel with --channel. Connected: " + ", ".join(c["channel_title"] for c in chans))
    hits = [c for c in chans if name.lower() in c["channel_title"].lower() or name == c["channel_id"]]
    if len(hits) != 1:
        sys.exit(f"--channel '{name}' matched {len(hits)} channels. Connected: "
                 + ", ".join(c["channel_title"] for c in chans))
    return hits[0]


def upload_to_drive(srv, channel, path):
    size = path.stat().st_size
    state_file = path.with_name(path.name + ".upload.json")
    tok = srv.req("POST", "/api/drive/token", json={"channel_account_id": channel["id"]})

    def auth():
        return {"Authorization": "Bearer " + tok["access_token"]}

    def refresh():
        tok.update(srv.req("POST", "/api/drive/token", json={"channel_account_id": channel["id"]}))

    def status(url):
        r = requests.put(url, headers={**auth(), "Content-Range": f"bytes */{size}", "Content-Length": "0"},
                         timeout=60)
        if r.status_code in (200, 201):
            return r.json()
        if r.status_code == 308:
            rng = r.headers.get("Range")
            return int(rng.split("-")[1]) + 1 if rng else 0
        if r.status_code == 401:
            refresh()
            return status(url)
        return -1

    url, offset = None, 0
    if state_file.exists():
        st = json.loads(state_file.read_text())
        if st.get("size") == size and st.get("drive_account_id") == tok["drive_account_id"]:
            res = status(st["url"])
            if isinstance(res, dict):
                state_file.unlink()
                return res["id"], tok["drive_account_id"]
            if res >= 0:
                url, offset = st["url"], res
                print(f"Resuming upload at {offset * 100 // size}%")
    if not url:
        r = requests.post(DRIVE_UPLOAD, params={"uploadType": "resumable", "fields": "id"},
                          headers={**auth(), "X-Upload-Content-Type": "video/mp4",
                                   "X-Upload-Content-Length": str(size)},
                          json={"name": path.name, "parents": [tok["folder_id"]]}, timeout=60)
        if not r.ok:
            raise RuntimeError(f"Drive refused the upload: {r.status_code} {r.text[:300]}")
        url = r.headers["Location"]
        state_file.write_text(json.dumps({"url": url, "size": size, "drive_account_id": tok["drive_account_id"]}))

    failures, t0, sent0 = 0, time.time(), offset
    with path.open("rb") as f:
        while True:
            f.seek(offset)
            data = f.read(CHUNK)
            try:
                r = requests.put(url, data=data, timeout=900, headers={
                    **auth(), "Content-Length": str(len(data)),
                    "Content-Range": f"bytes {offset}-{offset + len(data) - 1}/{size}"})
                if r.status_code == 401:
                    refresh()
                    continue
                if r.status_code in (200, 201):
                    state_file.unlink(missing_ok=True)
                    print(f"\rUploaded 100%{' ' * 30}")
                    return r.json()["id"], tok["drive_account_id"]
                if r.status_code == 308:
                    rng = r.headers.get("Range")
                    offset = int(rng.split("-")[1]) + 1 if rng else 0
                    failures = 0
                    speed = (offset - sent0) / max(1, time.time() - t0) / 1024 / 1024
                    print(f"\rUploading to Drive: {offset * 100 // size}%  ({speed:.1f} MB/s)   ", end="", flush=True)
                    continue
                raise IOError(f"Drive answered {r.status_code}")
            except (IOError, requests.RequestException) as e:
                failures += 1
                if failures > 8:
                    sys.exit(f"\nUpload keeps failing ({e}). Run the same command again later to continue.")
                time.sleep(min(60, 2 ** failures))
                res = status(url)
                if isinstance(res, dict):
                    state_file.unlink(missing_ok=True)
                    return res["id"], tok["drive_account_id"]
                if res < 0:
                    state_file.unlink(missing_ok=True)
                    sys.exit("\nThe upload session expired. Run the same command again to start over.")
                offset = res


def find_script(video, given=None):
    """The narration script: --script file, else AutoDoc's project.json next to the video."""
    cands = [Path(given)] if given else [video.parent / "project.json", video.parent / "script.txt"]
    for c in cands:
        if not c.exists():
            continue
        if c.suffix.lower() == ".json":
            try:
                txt = json.loads(c.read_text(encoding="utf-8")).get("script", "")
            except Exception:
                txt = ""
        else:
            txt = c.read_text(encoding="utf-8", errors="replace")
        if txt.strip():
            return txt
    if given:
        sys.exit(f"No script text found in {given}")
    return ""


def main():
    p = argparse.ArgumentParser(description="Send a video to the NMR YouTube scheduler")
    p.add_argument("video", nargs="?")
    p.add_argument("--setup", action="store_true", help="save server address and app password")
    p.add_argument("--status", action="store_true", help="show the queue")
    p.add_argument("--channel", help="part of the channel name")
    p.add_argument("--title")
    p.add_argument("--description", default="")
    p.add_argument("--description-file")
    p.add_argument("--tags", default="", help="comma separated")
    p.add_argument("--time", help="'YYYY-MM-DD HH:MM' in the server's timezone; leave out to decide later")
    p.add_argument("--thumbnail", help="your own thumbnail; without it, Flow makes 6 options on the server")
    p.add_argument("--no-auto-thumb", action="store_true", help="don't make Flow thumbnail options")
    p.add_argument("--script", help="script .txt or AutoDoc project.json (found next to the video automatically)")
    p.add_argument("--no-auto-meta", action="store_true", help="don't let Claude write title + description")
    p.add_argument("--playlist", help="playlist ID")
    p.add_argument("--category", default="27", help="YouTube category ID (27 = Education)")
    p.add_argument("--kids", action="store_true", help="made for kids")
    p.add_argument("--ai", action="store_true", help="contains altered or synthetic content")
    a = p.parse_args()

    if a.setup:
        server = input("Server address [https://dayroom-cinch-scorer.ngrok-free.dev]: ").strip() \
            or "https://dayroom-cinch-scorer.ngrok-free.dev"
        pw = input("App password: ").strip()
        CONF.write_text(json.dumps({"server": server, "password": pw}))
        Server(load_conf()).req("GET", "/api/channels")
        print("Saved and connected.")
        return

    srv = Server(load_conf())
    if a.status:
        for v in srv.req("GET", "/api/videos"):
            when = v["posted_at_local"] or v["publish_at_local"] or "no time"
            print(f"#{v['id']:<4} {v['status']:<10} {when:<17} {v['channel'] or '':<20} {v['title'] or v['file_name']}")
        return
    if not a.video:
        p.error("give the video file")
    path = Path(a.video)
    if not path.is_file():
        sys.exit(f"File not found: {path}")

    channel = pick_channel(srv, a.channel)
    desc = Path(a.description_file).read_text(encoding="utf-8") if a.description_file else a.description
    print(f"Channel: {channel['channel_title']}  |  {path.name}  ({path.stat().st_size / 1024 / 1024:.0f} MB)")
    file_id, drive_acc = upload_to_drive(srv, channel, path)
    script = "" if a.no_auto_meta else find_script(path, a.script)
    v = srv.req("POST", "/api/videos", json={
        "channel_account_id": channel["id"], "drive_account_id": drive_acc, "drive_file_id": file_id,
        "file_name": path.name, "file_size": path.stat().st_size, "title": a.title or path.stem,
        "description": desc, "tags": a.tags, "publish_at": a.time, "playlist_id": a.playlist,
        "category_id": a.category, "made_for_kids": a.kids, "synthetic_media": a.ai,
        "auto_thumbnail": not a.thumbnail and not a.no_auto_thumb, "script": script})
    if a.thumbnail:
        with open(a.thumbnail, "rb") as f:
            v = srv.req("POST", f"/api/videos/{v['id']}/thumbnail", files={"file": f})
    print(f"Queued #{v['id']}: {v['title']}  ->  {v['publish_at_local'] or 'no time yet (set it in the app)'}")
    if script:
        print(f"Script sent ({len(script.split())} words): Haiku summarizes it, Opus writes 3 titles + description"
              + (" + the thumbnail prompts." if not a.thumbnail and not a.no_auto_thumb else "."))
    if not a.thumbnail and not a.no_auto_thumb:
        print("Thumbnails: Claude writes 6 prompts, Flow makes them, you pick in the app (or Claude picks 2 h before posting).")
    print("This video no longer needs the PC.")


if __name__ == "__main__":
    main()
