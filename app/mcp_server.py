"""Remote MCP endpoint so Claude (claude.ai / the Claude app) can run the scheduler by chat.

Add it in Claude: Settings -> Connectors -> Add custom connector, URL = https://<domain>/mcp/<token>.
The token is the secret: anyone with the full link can control the scheduler. Get it from the app
(Settings -> Connect Claude) or /api/mcp-url. Minimal Streamable-HTTP MCP (JSON responses, no sessions).
"""
import base64
import io
import json
import secrets

from fastapi import Body, Depends, HTTPException, Request
from fastapi.responses import JSONResponse, Response
from PIL import Image
from starlette.concurrency import run_in_threadpool

from . import db
from .config import BASE_URL

PROTOCOLS = ("2025-06-18", "2025-03-26", "2024-11-05")


def token():
    t = db.kv_get("mcp_token")
    if not t:
        t = secrets.token_urlsafe(24)
        db.kv_set("mcp_token", t)
    return t


def _schema(props=None, required=()):
    return {"type": "object", "properties": props or {}, "required": list(required)}


VID = {"video_id": {"type": "integer", "description": "video id from list_videos"}}
TOOLS = [
    ("list_channels", "Connected YouTube channels (id, name).", _schema()),
    ("list_videos", "Videos in the scheduler with status, channel, posting time, title, thumbnail/title progress. "
     "status filter: draft, scheduled, posting, posted, failed (comma separated) or empty for all.",
     _schema({"status": {"type": "string"}})),
    ("get_video", "Everything about one video: title, description, tags, time, title options, thumbnail state, "
     "notes, errors, YouTube link.", _schema(VID, ["video_id"])),
    ("update_video", "Change a video. Before posting: title, description, tags, publish_time ('YYYY-MM-DD HH:MM' in the "
     "scheduler's timezone, or '' to make it a draft). After posting: title/description/tags only (updates YouTube).",
     _schema({**VID, "title": {"type": "string"}, "description": {"type": "string"},
              "tags": {"type": "string", "description": "comma separated"}, "publish_time": {"type": "string"}},
             ["video_id"])),
    ("pick_title", "Use one of Claude's title options (1-based number from get_video).",
     _schema({**VID, "option": {"type": "integer"}}, ["video_id", "option"])),
    ("more_titles", "Ask Opus for 3 more title options.", _schema(VID, ["video_id"])),
    ("view_thumbnails", "See the Flow thumbnail options as images (numbered), plus which one is in use.",
     _schema(VID, ["video_id"])),
    ("pick_thumbnail", "Use thumbnail option N (sets it on YouTube too if already posted).",
     _schema({**VID, "option": {"type": "integer"}}, ["video_id", "option"])),
    ("make_thumbnails", "kind=remake: 6 fresh options (replaces). kind=new: 3 more new ideas. kind=like: 3 variations of "
     "option N. kind=edit: 1 edited copy of option N following note (e.g. 'make the text bigger').",
     _schema({**VID, "kind": {"type": "string", "enum": ["remake", "new", "like", "edit"]},
              "option": {"type": "integer"}, "note": {"type": "string"}}, ["video_id", "kind"])),
    ("retry_flow", "Thumbnails stuck (Google flag / stuck prompt): start this video's thumbnails again in a new Flow "
     "project with the same prompts. Only after the user checked Flow works again.", _schema(VID, ["video_id"])),
    ("more_prompts", "Write thumbnail PROMPTS only (no images) for nmr to paste into Flow by hand: kind=new (3 new ideas) "
     "or kind=like with option N (3 similar to that option). Read them later with get_video (extra_prompts).",
     _schema({**VID, "kind": {"type": "string", "enum": ["new", "like"]}, "option": {"type": "integer"}},
             ["video_id", "kind"])),
    ("post_now", "Upload this video to YouTube right now.", _schema(VID, ["video_id"])),
    ("retry_video", "Retry a failed upload.", _schema(VID, ["video_id"])),
    ("delete_video", "Delete a video from the scheduler and its Drive copy (not from YouTube). Only after the user "
     "clearly confirmed.", _schema(VID, ["video_id"])),
    ("flow_status", "Latest Google Flow alert (captcha, stuck, daily limit) and thumbnail jobs in progress.", _schema()),
    ("get_channel_style", "A channel's thumbnail notes and the text added to every description.",
     _schema({"channel_id": {"type": "integer"}}, ["channel_id"])),
    ("set_channel_style", "Set a channel's thumbnail notes and/or description end text.",
     _schema({"channel_id": {"type": "integer"}, "thumbnail_notes": {"type": "string"},
              "description_footer": {"type": "string"}}, ["channel_id"])),
]


def _short(v):
    return {k: v.get(k) for k in ("id", "status", "channel", "title", "publish_at_local", "posted_at_local",
                                  "thumb_state", "meta_state", "youtube_url", "error")}


def _img(path):
    im = Image.open(path).convert("RGB")
    im.thumbnail((640, 360))
    buf = io.BytesIO()
    im.save(buf, "JPEG", quality=80)
    return {"type": "image", "data": base64.b64encode(buf.getvalue()).decode(), "mimeType": "image/jpeg"}


def run_tool(m, name, a):
    vid = int(a["video_id"]) if "video_id" in a else None
    if name == "list_channels":
        return [{"id": c["id"], "name": c["channel_title"]} for c in m.channels() if c["channel_id"]]
    if name == "list_videos":
        return [_short(v) for v in m.list_videos(a.get("status", ""))]
    if name == "get_video":
        return m.get_video(vid)
    if name == "update_video":
        data = {k: a[k] for k in ("title", "description", "tags") if k in a}
        if "publish_time" in a:
            data["publish_at"] = a["publish_time"] or None
        return _short(m.edit_video(vid, data)) | {"title": m.get_video(vid)["title"]}
    if name == "pick_title":
        opts = m.get_video(vid)["title_options"]
        n = int(a["option"])
        if not 1 <= n <= len(opts):
            raise ValueError(f"there are {len(opts)} title options")
        return _short(m.edit_video(vid, {"title": opts[n - 1]}))
    if name == "more_titles":
        m.more_titles(vid)
        return "Opus is writing 3 more titles; check get_video in a minute or two."
    if name == "view_thumbnails":
        v = m.get_video(vid)
        opts = json.loads(m._get_video(vid).get("thumb_options") or "[]")
        content = [{"type": "text", "text": f"{len(opts)} options; in use: {v['thumb_pick'] or 'none'}; state: "
                                            f"{v['thumb_state']}; {v['thumb_note'] or ''}"}]
        for k, p in enumerate(opts, 1):
            content += [{"type": "text", "text": f"Option {k}:"}, _img(p)]
        return {"_content": content}
    if name == "pick_thumbnail":
        return _short(m.thumb_pick(vid, {"option": int(a["option"])}))
    if name == "make_thumbnails":
        kind = a["kind"]
        if kind == "remake":
            m.auto_thumbnail(vid)
        else:
            m.more_thumbnails(vid, {"kind": kind, "option": a.get("option"), "note": a.get("note", "")})
        return "Started. Claude writes the prompts, then Flow makes them (a few minutes); use view_thumbnails later."
    if name == "more_prompts":
        m.more_prompts(vid, {"kind": a["kind"], "option": a.get("option")})
        return "Claude is writing them; check get_video (extra_prompts) in a minute or two."
    if name == "retry_flow":
        return _short(m.retry_flow(vid))
    if name == "post_now":
        return _short(m.post_now(vid))
    if name == "retry_video":
        return _short(m.retry(vid))
    if name == "delete_video":
        return m.delete_video(vid)
    if name == "flow_status":
        busy = db.q("SELECT id, title, thumb_state, thumb_note FROM videos WHERE thumb_state IN "
                    "('prompting','queued','generating')")
        return {"last_alert": m.flow_alert(), "in_progress": busy}
    if name == "get_channel_style":
        return m.get_thumb_style(int(a["channel_id"]))
    if name == "set_channel_style":
        cur = m.get_thumb_style(int(a["channel_id"]))
        return m.put_thumb_style(int(a["channel_id"]), {
            "style": a.get("thumbnail_notes", cur["style"]), "footer": a.get("description_footer", cur["footer"])})
    raise ValueError(f"unknown tool {name}")


def handle(m, msg):
    mid, method, params = msg.get("id"), msg.get("method"), msg.get("params") or {}
    if mid is None:  # notification
        return None
    ok = lambda result: {"jsonrpc": "2.0", "id": mid, "result": result}  # noqa: E731
    if method == "initialize":
        want = params.get("protocolVersion")
        return ok({"protocolVersion": want if want in PROTOCOLS else PROTOCOLS[0],
                   "capabilities": {"tools": {}},
                   "serverInfo": {"name": "nmr-scheduler", "version": "1.0"},
                   "instructions": "NMR Scheduler: nmr's YouTube upload scheduler for his channels. Times are in the "
                                   "scheduler's timezone. Use list_videos first to find video ids."})
    if method == "ping":
        return ok({})
    if method == "tools/list":
        return ok({"tools": [{"name": n, "description": d, "inputSchema": s} for n, d, s in TOOLS]})
    if method == "tools/call":
        try:
            out = run_tool(m, params.get("name"), params.get("arguments") or {})
            content = out["_content"] if isinstance(out, dict) and "_content" in out else \
                [{"type": "text", "text": out if isinstance(out, str) else json.dumps(out, default=str, indent=1)}]
            return ok({"content": content, "isError": False})
        except HTTPException as e:
            return ok({"content": [{"type": "text", "text": str(e.detail)}], "isError": True})
        except Exception as e:  # noqa: BLE001
            return ok({"content": [{"type": "text", "text": f"{e.__class__.__name__}: {e}"}], "isError": True})
    return {"jsonrpc": "2.0", "id": mid, "error": {"code": -32601, "message": f"unknown method {method}"}}


def register(app, m):
    @app.post("/mcp/{tok}")
    async def mcp_post(tok: str, request: Request):
        if not secrets.compare_digest(tok, token()):
            raise HTTPException(404)
        body = await request.json()
        if isinstance(body, list):
            out = [r for r in [await run_in_threadpool(handle, m, x) for x in body] if r is not None]
            return JSONResponse(out) if out else Response(status_code=202)
        r = await run_in_threadpool(handle, m, body)
        return JSONResponse(r) if r is not None else Response(status_code=202)

    @app.get("/mcp/{tok}")
    def mcp_get(tok: str):
        return Response(status_code=405, headers={"Allow": "POST"})

    @app.delete("/mcp/{tok}")
    def mcp_delete(tok: str):
        return Response(status_code=200)

    @app.get("/api/mcp-url", dependencies=[Depends(m.need_auth)])
    def mcp_url():
        return {"url": f"{BASE_URL}/mcp/{token()}"}

    @app.post("/api/mcp-url/reset", dependencies=[Depends(m.need_auth)])
    def mcp_reset(_: dict = Body(default={})):
        db.kv_set("mcp_token", secrets.token_urlsafe(24))
        return {"url": f"{BASE_URL}/mcp/{token()}"}
