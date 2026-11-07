"""AutoDoc page: NMR Scheduler thumbnail prompts, ready to paste into Google Flow's Agent.

Put this file in AutoDoc's  pages\\  folder (next to app.py). Each box has a copy button (top right).
It reads the scheduler address and password from D:\\yt-scheduler-server\\yt_scheduler_pc.json.
"""
import json
from pathlib import Path

import requests
import streamlit as st

CONF = Path(r"D:\yt-scheduler-server\yt_scheduler_pc.json")
STATE = {"prompting": "Claude is writing the prompts…", "prompts_ready": "Prompts ready",
         "waiting_images": "Waiting for the Flow images", "ready": "Options ready to pick",
         "picked": "Thumbnail picked", "failed": "Failed"}

st.title("Thumbnail prompts")
st.caption("Copy a video's Flow Agent message (copy icon, top right of the box), open Flow, make a new project, "
           "turn on Agent and paste. The Flow extension uploads the images to that video by itself.")

try:
    conf = json.loads(CONF.read_text())
except OSError:
    st.error(f"Can't read {CONF}. Run send_to_queue.py --setup first.")
    st.stop()
BASE = conf["server"].rstrip("/")
H = {"Authorization": "Bearer " + conf["password"]}


def code(text):
    try:
        st.code(text, language=None, wrap_lines=True)
    except TypeError:   # older Streamlit
        st.code(text, language=None)


def api(method, path):
    r = requests.request(method, BASE + path, headers=H, timeout=60)
    if not r.ok:
        raise RuntimeError(f"{r.status_code}: {r.text[:200]}")
    return r.json()


try:
    videos = [v for v in api("GET", "/api/videos") if v["status"] in ("rendering", "draft", "scheduled", "failed")]
except Exception as e:  # noqa: BLE001
    st.error(f"Can't reach the scheduler: {e}")
    st.stop()

if st.button("Refresh"):
    st.rerun()
if not videos:
    st.info("No videos waiting.")

for v in videos:
    state = STATE.get(v.get("thumb_state") or "", v.get("thumb_state") or "no thumbnails yet")
    head = f"#{v['id']} · {v.get('channel') or ''} · {v['title'] or v.get('file_name')}  —  {state}"
    with st.expander(head, expanded=v.get("thumb_state") in ("prompts_ready", "waiting_images")):
        if v.get("thumb_options"):
            st.write(f"{v['thumb_options']} options made so far.")
        key = f"msg{v['id']}"
        c1, c2, c3 = st.columns(3)
        if c1.button("Flow Agent message", key=f"a{v['id']}", disabled=not v.get("thumb_prompts")):
            try:
                st.session_state[key] = api("POST", f"/api/videos/{v['id']}/agent-copy")["text"]
            except Exception as e:  # noqa: BLE001
                st.error(str(e))
        if c2.button("+ 3 new prompts", key=f"n{v['id']}"):
            api("POST", f"/api/videos/{v['id']}/more-prompts")
            st.toast("Claude is writing 3 more; press Refresh in a minute.")
        if c3.button("Rewrite all prompts", key=f"r{v['id']}"):
            api("POST", f"/api/videos/{v['id']}/auto-thumbnail")
            st.toast("Rewriting; press Refresh in a minute.")
        if st.session_state.get(key):
            st.caption("Paste this into the Flow Agent:")
            code(st.session_state[key])
        prompts = v.get("thumb_prompts") or []
        extra = v.get("extra_prompts") or []
        if prompts or extra:
            st.caption("Single prompts (for pasting one at a time):")
            for p in prompts:
                code(p)
            for e in extra:
                st.caption(e.get("label", ""))
                code(e.get("prompt", ""))
