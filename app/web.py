LOGIN = """<!doctype html><html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>NMR Scheduler</title><style>
body{font-family:system-ui,sans-serif;background:#0f1115;color:#e8e8e8;display:flex;min-height:100vh;align-items:center;justify-content:center;margin:0}
form{background:#1a1d24;padding:28px;border-radius:12px;width:min(340px,90vw)}
input,button{width:100%;padding:12px;margin-top:12px;border-radius:8px;border:1px solid #333;background:#0f1115;color:#fff;font-size:16px;box-sizing:border-box}
button{background:#e53935;border:0;font-weight:600;cursor:pointer}.err{color:#ff8a80}
</style></head><body><form method="post" action="/login"><h2 style="margin:0">NMR Scheduler</h2>
<input type="password" name="password" placeholder="App password" autofocus><button>Log in</button>
<p class="err">{{error}}</p></form></body></html>"""

DASHBOARD = r"""<!doctype html><html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>NMR Scheduler</title><style>
*{box-sizing:border-box}body{font-family:system-ui,sans-serif;background:#0f1115;color:#e8e8e8;margin:0;padding:16px;max-width:900px;margin:auto}
h1{font-size:20px;margin:4px 0 12px}h2{font-size:16px;margin:20px 0 8px}
.banner{background:#4a3500;color:#ffd54f;padding:10px 12px;border-radius:8px;font-size:14px}
.card{background:#1a1d24;border-radius:10px;padding:12px;margin:8px 0;display:flex;gap:12px;flex-wrap:wrap}
.thumb{width:160px;height:90px;background:#2a2e38;border-radius:6px;object-fit:cover;flex:none}
.grow{flex:1;min-width:200px}.muted{color:#9aa0aa;font-size:13px}.err{color:#ff8a80;font-size:13px;word-break:break-word}
.st{display:inline-block;font-size:12px;padding:2px 8px;border-radius:10px;background:#333}
.st.scheduled{background:#1e3a5f}.st.posted{background:#1b5e20}.st.failed{background:#7f1d1d}.st.posting{background:#6d4c00}
button,.btn{background:#2a2e38;color:#fff;border:0;border-radius:8px;padding:8px 12px;margin:4px 4px 0 0;cursor:pointer;font-size:14px;text-decoration:none;display:inline-block}
.primary{background:#e53935}.tabs button.on{background:#e53935}
input,textarea,select{width:100%;padding:8px;margin:4px 0;border-radius:6px;border:1px solid #333;background:#0f1115;color:#fff;font-size:14px}
textarea{min-height:90px}label{font-size:13px;color:#9aa0aa}.row{display:flex;gap:8px}.row>*{flex:1}
.chk{display:flex;align-items:center;gap:6px;font-size:14px}.chk input{width:auto}
</style></head><body>
<h1>NMR YouTube Scheduler</h1>
<div class="banner" id="testbanner" style="display:none">Test mode: YouTube keeps every upload private until the API audit is approved. Use throwaway clips only.</div>
<h2>Channels and storage</h2>
<div class="banner" id="nodrive" style="display:none;margin-bottom:8px">Step 2: connect Google Drive so the PC has somewhere to put videos.</div>
<div id="channels"></div>
<a class="btn primary" href="/connect">+ Add YouTube channel</a> <a class="btn" href="/connect-drive">+ Connect Google Drive</a>
<h2>Queue</h2>
<div class="tabs"><button data-t="draft,scheduled,posting" class="on">Upcoming</button><button data-t="posted">Posted</button><button data-t="failed">Failed</button></div>
<div id="videos"></div>
<script>
const TZ="{{tz}}";if("{{test}}"==="1")testbanner.style.display="block";
let tab="draft,scheduled,posting",editing=null,chans=[];
const $=(s,e=document)=>e.querySelector(s);
async function api(p,o={}){const r=await fetch(p,o);if(!r.ok){alert((await r.text()).slice(0,300));throw r}return r.json()}
function esc(s){return (s??"").toString().replace(/[&<>"]/g,c=>({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;"}[c]))}
async function loadChannels(){chans=await api("/api/channels");nodrive.style.display=chans.some(c=>c.has_drive&&c.is_storage)?"none":"block";
channels.innerHTML=chans.map(c=>`<div class="card"><div class="grow">
<b>${esc(c.channel_title||"Drive only")}</b> <span class="muted">${esc(c.email||"")}</span>
${c.is_storage&&c.has_drive?' <span class="st posted">Drive storage</span>':''}${c.has_drive&&!c.is_storage?' <span class="st">Drive connected</span>':''}${c.needs_reconnect?' <span class="st failed">Reconnect needed</span>':''}
<div>${c.has_drive&&!c.is_storage?`<button onclick="storage(${c.id})">Use this Drive for storage</button>`:''}
<button onclick="removeAcc(${c.id})">Remove</button></div></div></div>`).join("")||'<p class="muted">No channels yet.</p>'}
async function storage(id){await api(`/api/accounts/${id}/storage`,{method:"POST"});loadChannels()}
async function removeAcc(id){if(confirm("Remove this account from the scheduler?")){await api(`/api/accounts/${id}`,{method:"DELETE"});loadChannels()}}
function local(v){return v.publish_at_local?v.publish_at_local.replace(" ","T"):""}
async function loadVideos(){if(editing)return;const vs=await api("/api/videos?status="+tab);
videos.innerHTML=vs.map(v=>`<div class="card" id="v${v.id}">
${v.has_thumbnail?`<img class="thumb" src="/api/videos/${v.id}/thumbnail?t=${Date.now()}">`:'<div class="thumb"></div>'}
<div class="grow"><b>${esc(v.title||v.file_name)}</b><div class="muted">${esc(v.channel||"")} · ${v.status==="posted"?"posted "+esc(v.posted_at_local):(v.publish_at_local?esc(v.publish_at_local)+" ("+TZ+")":"no time set")}</div>
<span class="st ${v.status}">${v.status}</span> ${v.youtube_url?`<a href="${v.youtube_url}" target="_blank" style="color:#90caf9">${v.youtube_url}</a>`:""}
${v.error?`<div class="err">${esc(v.error)}</div>`:""}
<div>${["draft","scheduled","failed"].includes(v.status)?`<button onclick="edit(${v.id})">Edit</button><button onclick="postNow(${v.id})">Post now</button>`:""}
${v.status==="failed"?`<button onclick="retry(${v.id})">Retry</button>`:""}
${v.status!=="posting"?`<button onclick="del(${v.id})">Delete</button>`:""}</div></div></div>`).join("")||'<p class="muted">Nothing here.</p>'}
async function edit(id){const v=await api("/api/videos/"+id);editing=id;const el=$("#v"+id);
el.innerHTML=`<div class="grow"><label>Title (${(v.title||"").length}/100)</label><input id="f_title" maxlength="100" value="${esc(v.title)}">
<label>Description</label><textarea id="f_desc">${esc(v.description)}</textarea>
<label>Tags (comma separated)</label><input id="f_tags" value="${esc(v.tags.join(", "))}">
<div class="row"><div><label>Post at (${TZ})</label><input id="f_time" type="datetime-local" value="${local(v)}"></div>
<div><label>Channel</label><select id="f_ch">${chans.filter(c=>c.channel_id).map(c=>`<option value="${c.id}" ${c.id==v.channel_account_id?"selected":""}>${esc(c.channel_title)}</option>`).join("")}</select></div></div>
<label>Playlist</label><select id="f_pl"><option value="">None</option></select>
<label class="chk"><input type="checkbox" id="f_kids" ${v.made_for_kids?"checked":""}> Made for kids</label>
<label class="chk"><input type="checkbox" id="f_ai" ${v.synthetic_media?"checked":""}> Altered or synthetic content (AI)</label>
<label>Thumbnail</label><input type="file" id="f_thumb" accept="image/*">
<button class="primary" onclick="save(${id})">Save</button><button onclick="editing=null;loadVideos()">Cancel</button></div>`;
const loadPl=async()=>{const pls=await api(`/api/channels/${$("#f_ch").value}/playlists`).catch(()=>[]);
$("#f_pl").innerHTML='<option value="">None</option>'+pls.map(p=>`<option value="${p.id}" ${p.id==v.playlist_id?"selected":""}>${esc(p.title)}</option>`).join("")};
$("#f_ch").onchange=loadPl;loadPl()}
async function save(id){const body={title:$("#f_title").value,description:$("#f_desc").value,tags:$("#f_tags").value,
publish_at:$("#f_time").value||null,channel_account_id:+$("#f_ch").value,playlist_id:$("#f_pl").value||null,
made_for_kids:$("#f_kids").checked,synthetic_media:$("#f_ai").checked};
await api("/api/videos/"+id,{method:"PATCH",headers:{"Content-Type":"application/json"},body:JSON.stringify(body)});
const f=$("#f_thumb").files[0];if(f){const fd=new FormData();fd.append("file",f);await api(`/api/videos/${id}/thumbnail`,{method:"POST",body:fd})}
editing=null;loadVideos()}
async function postNow(id){if(confirm("Upload this video to YouTube now?")){await api(`/api/videos/${id}/post-now`,{method:"POST"});loadVideos()}}
async function retry(id){await api(`/api/videos/${id}/retry`,{method:"POST"});loadVideos()}
async function del(id){if(confirm("Delete from the queue (and from Drive if not posted)?")){await api("/api/videos/"+id,{method:"DELETE"});loadVideos()}}
document.querySelectorAll(".tabs button").forEach(b=>b.onclick=()=>{document.querySelectorAll(".tabs button").forEach(x=>x.classList.remove("on"));b.classList.add("on");tab=b.dataset.t;editing=null;loadVideos()});
loadChannels();loadVideos();setInterval(loadVideos,20000);
</script></body></html>"""
