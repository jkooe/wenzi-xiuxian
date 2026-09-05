"""联网文字修仙 · 阶段0 服务端（FastAPI + uvicorn + SQLite）。

能力边界（阶段0 地基）：
  - 账号：注册 / 登录（PBKDF2 加盐哈希 + token 登录态）
  - 存档上云：每个账号独立存档，落 SQLite；每次操作自动落库（不再丢失内存态）
  - 取代旧 web_server 的「匿名 sid + 内存态」
后续阶段（暂未实现）：同服可见（在线列表/世界聊天）、实时对战（WebSocket）、战力榜。

启动：./run.sh server   （或 ./.venv/bin/python -m uvicorn app:app --host 0.0.0.0 --port 8000）
"""

from __future__ import annotations

import contextlib
import io
import sys
import threading
import time
from pathlib import Path

_ROOT = Path(__file__).resolve().parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

import uvicorn  # noqa: E402
from fastapi import (  # noqa: E402
    Depends,
    FastAPI,
    HTTPException,
    Request,
    WebSocket,
    WebSocketDisconnect,
)
from fastapi.responses import HTMLResponse  # noqa: E402
from pydantic import BaseModel  # noqa: E402

import account  # noqa: E402
import db  # noqa: E402
import web_views  # noqa: E402
import world  # noqa: E402
from xiuxian.factory import create_game  # noqa: E402
from xiuxian.ui.cli import CLI  # noqa: E402

db.init_db()

# ---------- 内嵌前端（登录 / 游戏面板 / 秘境弹窗） ----------
_PAGE = """<!DOCTYPE html>
<html lang="zh">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>联网文字修仙</title>
<style>
  :root{
    --bg:#0d0f1a; --panel:#161a2c; --panel2:#1f2440; --ink:#e8eaf6;
    --muted:#9aa0c0; --gold:#e6c068; --red:#e06a6a; --cyan:#5fd0e6;
    --green:#6ad08a; --line:#2a3050;
  }
  *{box-sizing:border-box}
  body{margin:0;background:linear-gradient(160deg,#0b0d18,#10131f 60%);
    color:var(--ink);font-family:-apple-system,"PingFang SC","Microsoft YaHei",sans-serif;
    min-height:100vh}
  .hidden{display:none!important}
  .screen{max-width:560px;margin:0 auto;padding:18px}
  header{display:flex;align-items:center;justify-content:space-between;
    padding:16px 18px;background:linear-gradient(90deg,#1a1f3a,#222a52);
    border-bottom:1px solid var(--line);position:sticky;top:0;z-index:5}
  header h2{margin:0;font-size:18px;color:var(--gold);letter-spacing:2px}
  .card{background:var(--panel);border:1px solid var(--line);border-radius:14px;
    padding:14px;margin:12px 0;box-shadow:0 6px 22px rgba(0,0,0,.35)}
  .peer{display:flex;justify-content:space-between;align-items:center;
    padding:7px 0;border-bottom:1px dashed var(--line);font-size:13px}
  .peer:last-child{border-bottom:none}
  .peer .nm{color:var(--ink)} .peer .nm.me{color:var(--gold);font-weight:700}
  .peer .dot{display:inline-block;width:7px;height:7px;border-radius:50%;
    background:#5a5f7a;margin-right:5px;vertical-align:1px}
  .peer .dot.on{background:var(--green)}
  .peer .sub{color:var(--muted);font-size:12px}
  .peer .pw{color:var(--cyan);font-weight:600;white-space:nowrap}
  .peer .rk{color:var(--gold);font-weight:700;margin-right:6px}
  input{width:100%;padding:11px 12px;margin:7px 0;border-radius:9px;border:1px solid var(--line);
    background:var(--panel2);color:var(--ink);font-size:15px;outline:none}
  input:focus{border-color:var(--gold)}
  button{cursor:pointer;border:none;border-radius:9px;padding:10px 14px;font-size:14px;
    background:var(--panel2);color:var(--ink);border:1px solid var(--line);transition:.15s}
  button:hover{border-color:var(--gold)}
  button.primary{background:linear-gradient(90deg,#3a2f6e,#5a3fae);border:none;color:#fff}
  button.danger{color:var(--red)}
  .row{display:flex;gap:8px;flex-wrap:wrap}
  .row button{flex:1 1 auto}
  .bar{height:11px;border-radius:6px;background:#0c0e18;overflow:hidden;margin:4px 0 9px}
  .bar > i{display:block;height:100%;border-radius:6px}
  .lab{display:flex;justify-content:space-between;font-size:13px;color:var(--muted)}
  .grid{display:grid;grid-template-columns:1fr 1fr;gap:6px 14px;font-size:13px;margin-top:6px}
  .grid div{display:flex;justify-content:space-between;border-bottom:1px dashed var(--line);padding:2px 0}
  .grid b{color:var(--gold);font-weight:600}
  .chips{display:flex;flex-wrap:wrap;gap:6px;margin-top:6px}
  .chip{background:var(--panel2);border:1px solid var(--line);border-radius:20px;
    padding:3px 10px;font-size:12px;color:var(--cyan)}
  .chip.rank{color:var(--gold)}
  #log{background:#0a0c16;border:1px solid var(--line);border-radius:10px;
    padding:10px;height:140px;overflow:auto;font-size:13px;line-height:1.6;color:#c9cef0}
  #log div{border-bottom:1px dotted #1c2138;padding:1px 0}
  #chat{background:#0a0c16;border:1px solid var(--line);border-radius:10px;
    padding:10px;height:160px;overflow:auto;font-size:13px;line-height:1.7;color:#c9cef0}
  #chat div{padding:1px 0}
  #chat .sys{color:var(--muted);font-size:12px}
  #chat .who{color:var(--gold);margin-right:6px}
  #chat .who.me{color:var(--cyan)}
  .act-grid{display:grid;grid-template-columns:1fr 1fr;gap:8px;margin-top:8px}
  .act-grid button{text-align:left}
  .act-grid button.primary{background:linear-gradient(90deg,#5a3fae,#7a55d8);color:#fff}
  .modal{position:fixed;inset:0;background:rgba(0,0,0,.6);display:flex;
    align-items:center;justify-content:center;z-index:20}
  .modal .box{background:var(--panel);border:1px solid var(--line);border-radius:14px;
    padding:18px;max-width:520px;width:92%;max-height:80vh;overflow:auto}
  .dungeon{border:1px solid var(--line);border-radius:10px;padding:10px;margin:8px 0}
  .dungeon h4{margin:0 0 4px;color:var(--gold)}
  .tag{font-size:12px;color:var(--muted)}
  .tag.ok{color:var(--green)} .tag.lock{color:var(--red)}
  #msg{color:var(--red);font-size:13px;min-height:18px;margin-top:6px}
  .sub{color:var(--muted);font-size:13px}
</style>
</head>
<body>

<div id="login" class="screen">
  <h1 style="text-align:center;color:var(--gold);letter-spacing:4px;margin-top:40px">联网文字修仙</h1>
  <p class="sub" style="text-align:center">开立道号，存档自在云端，换端续修无虞</p>
  <div class="card">
    <input id="lu" placeholder="道号（≤24字）" autocomplete="username">
    <input id="lp" type="password" placeholder="秘钥（≤64字）" autocomplete="current-password">
    <div class="row" style="margin-top:8px">
      <button class="primary" id="btnLogin">入道（登录）</button>
      <button id="btnReg">开立道号（注册）</button>
    </div>
    <div id="msg"></div>
  </div>
</div>

<div id="game" class="screen hidden">
  <header>
    <h2>联网文字修仙</h2>
    <div><span id="who" class="sub"></span>
      <button class="danger" id="btnLogout" style="margin-left:8px">离山</button></div>
  </header>
  <div id="statusCard" class="card">载入中…</div>
  <div class="card">
    <div class="lab"><span>同服修士</span><span class="sub" id="peerCount"></span></div>
    <div id="peers"><div class="sub">载入中…</div></div>
  </div>
  <div class="card">
    <div class="lab"><span>修行走势</span></div>
    <div id="log"></div>
  </div>
  <div class="card">
    <div class="lab"><span>世界聊天</span><span class="sub" id="chatState"></span></div>
    <div id="chat"></div>
    <div class="row" style="margin-top:8px">
      <input id="chatInput" placeholder="喊话同门修士…" maxlength="200">
      <button class="primary" id="btnChat">传话</button>
    </div>
  </div>
  <div class="card">
    <div class="lab"><span>施为</span><span class="sub">点按或输指令</span></div>
    <div id="actions" class="act-grid"></div>
    <div class="row" style="margin-top:10px">
      <input id="cmd" placeholder="如 breakthrough / hunt / explore / help">
      <button class="primary" id="btnCmd">施为</button>
    </div>
  </div>
</div>

<div id="catalogModal" class="modal hidden">
  <div class="box">
    <div class="lab"><h3 style="margin:0;color:var(--gold)">秘境目录</h3>
      <button id="btnCloseCat">关闭</button></div>
    <div id="catalogBody"></div>
  </div>
</div>

<script>
let token = localStorage.getItem('xx_token') || '';
let username = localStorage.getItem('xx_user') || '';

async function api(path, opts){
  opts = opts || {};
  const headers = {'Content-Type':'application/json'};
  if(token) headers['Authorization'] = 'Bearer '+token;
  const res = await fetch(path, Object.assign({headers}, opts));
  if(res.status === 401){ doLogout(); throw new Error('登录失效'); }
  return res;
}
function bar(ratio, color, label, val){
  ratio = Math.max(0, Math.min(1, ratio||0));
  return '<div class="lab"><span>'+label+'</span><span>'+val+'</span></div>'+
    '<div class="bar"><i style="width:'+(ratio*100).toFixed(1)+'%;background:'+color+'"></i></div>';
}
function renderStatus(d){
  let h = '<div style="font-size:17px;color:var(--gold)">'+d.name+' · '+d.title+' · '+d.realm+
    ' <span class="chip">'+d.realm_family+'</span></div>';
  h += '<div class="sub">第 '+d.day+' 日 · '+d.location+' · 灵气×'+d.density+
    ' · 下一境界：'+d.next+'</div>';
  h += bar(d.hp_ratio, 'var(--red)', '气血', d.hp+'/'+d.max_hp);
  h += bar(d.mp_ratio, 'var(--cyan)', '灵力', d.mp+'/'+d.max_mp);
  h += bar(d.stamina_ratio, 'var(--green)', '精力', d.stamina+'/100');
  h += bar(d.progress, 'var(--gold)', '修为', d.exp+' / '+(d.need==='圆满'?'圆满':d.need));
  h += '<div class="grid">';
  const g = [['攻击',d.atk],['防御',d.def],['身法',d.speed],['神识',d.spirit],
    ['悟性',d.comprehension],['根骨',d.physique],['气运',d.luck],['丹毒',d.poison],
    ['灵石',d.stones],['寿元',d.age+'/'+d.lifespan],['功法',d.art||'无'],['日常',d.quests+' 项']];
  g.forEach(function(x){ h += '<div><span>'+x[0]+'</span><b>'+x[1]+'</b></div>'; });
  h += '</div>';
  if(d.equip && d.equip.length) h += '<div class="sub" style="margin-top:8px">装备：'+d.equip.join('、')+'</div>';
  if(d.arts && d.arts.length){
    h += '<div class="chips">';
    d.arts.forEach(function(a){ h += '<span class="chip rank">'+a.name+'·'+a.rank+'</span>'; });
    h += '</div>';
  }
  if(d.buffs && d.buffs.length){
    h += '<div class="chips">';
    d.buffs.forEach(function(b){ h += '<span class="chip">'+b+'</span>'; });
    h += '</div>';
  }
  document.getElementById('statusCard').innerHTML = h;
}
function renderActions(actions){
  const box = document.getElementById('actions');
  box.innerHTML = '';
  actions.forEach(function(a){
    const b = document.createElement('button');
    if(a.primary) b.className = 'primary';
    b.textContent = a.label;
    b.onclick = function(){
      if(a.open_catalog){ openCatalog(); return; }
      sendCmd(a.cmd);
    };
    box.appendChild(b);
  });
}
function appendLog(logs, t){
  const el = document.getElementById('log');
  (logs||[]).forEach(function(line){
    const d = document.createElement('div');
    d.textContent = (t?('['+t+'] '):'') + line;
    el.appendChild(d);
  });
  el.scrollTop = el.scrollHeight;
}
async function sendCmd(line){
  if(!line) return;
  try{
    const res = await api('/api/command', {method:'POST',
      body: JSON.stringify({line:line})});
    const j = await res.json();
    appendLog(j.logs, j.time);
    poll();
  }catch(e){}
}
async function poll(){
  try{
    const res = await api('/api/status');
    const j = await res.json();
    renderStatus(j.data); renderActions(j.actions);
  }catch(e){}
}
async function loadPeers(){
  try{
    const res = await api('/api/rank');
    const j = await res.json();
    const rank = j.rank || [];
    const box = document.getElementById('peers');
    const cnt = document.getElementById('peerCount');
    const onlineN = rank.filter(function(x){return x.online;}).length;
    cnt.textContent = '在线 '+onlineN+' / 共 '+rank.length+' 人';
    let h = '';
    rank.forEach(function(x, i){
      h += '<div class="peer">'
        + '<span class="nm'+(x.username===username?' me':'')+'">'
        + '<span class="dot'+(x.online?' on':'')+'"></span>'
        + '<span class="rk">'+(i+1)+'</span>'+x.username+'</span>'
        + '<span class="sub">'+x.realm+'</span>'
        + '<span class="pw">战力 '+x.power+'</span></div>';
    });
    box.innerHTML = h || '<div class="sub">暂无修士</div>';
  }catch(e){}
}
async function openCatalog(){
  try{
    const res = await api('/api/catalog');
    const j = await res.json();
    let h = '';
    (j.catalog||[]).forEach(function(d){
      const lock = d.state.indexOf('可入') !== 0 && d.state !== '可入';
      h += '<div class="dungeon"><h4>'+d.name+' <span class="tag'+(lock?' lock':' ok')+'">'+d.state+'</span></h4>'+
        '<div class="tag">'+d.desc+'</div>'+
        '<div class="tag">层数 '+d.depth+' · 门槛 '+d.min_realm+' · 冷却 '+d.cooldown+' · 精力 '+d.stamina+'</div>'+
        '<div class="tag">'+d.reward+'</div></div>';
    });
    document.getElementById('catalogBody').innerHTML = h;
    document.getElementById('catalogModal').classList.remove('hidden');
  }catch(e){}
}
function closeCatalog(){ document.getElementById('catalogModal').classList.add('hidden'); }

function showLogin(){ document.getElementById('login').classList.remove('hidden');
  document.getElementById('game').classList.add('hidden'); }

// ---------- 阶段1b：世界聊天（WebSocket） ----------
let chatWs = null;
let chatRetry = 0;
function wsUrl(){
  const proto = location.protocol === 'https:' ? 'wss' : 'ws';
  return proto+'://'+location.host+'/ws/chat?token='+encodeURIComponent(token);
}
function appendChat(html, cls){
  const el = document.getElementById('chat');
  const d = document.createElement('div');
  if(cls) d.className = cls;
  d.innerHTML = html;
  el.appendChild(d);
  el.scrollTop = el.scrollHeight;
}
function esc(s){
  return String(s).replace(/[&<>"']/g, function(c){
    return {'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]; });
}
function connectChat(){
  if(!token) return;
  try{ chatWs = new WebSocket(wsUrl()); }catch(e){ return; }
  chatWs.onopen = function(){
    chatRetry = 0;
    document.getElementById('chatState').textContent = '已连通';
  };
  chatWs.onmessage = function(ev){
    let m; try{ m = JSON.parse(ev.data); }catch(e){ return; }
    if(m.type === 'chat'){
      const me = m.username === username ? ' me' : '';
      appendChat('<span class="who'+me+'">'+esc(m.username)+(m.realm?'（'+esc(m.realm)+'）':'')+
        '：</span>'+esc(m.text));
    }else if(m.type === 'presence'){
      appendChat('<span class="sys">'+esc(m.username)+(m.online?' 上线了':' 离线了')+'</span>', 'sys');
      loadPeers();          // 实时刷新同服修士面板（在线状态即时更新）
    }
  };
  chatWs.onclose = function(){
    document.getElementById('chatState').textContent = '已断开';
    if(token && chatRetry < 8){
      chatRetry++;
      setTimeout(connectChat, Math.min(2000 * chatRetry, 10000));   // 断线重连
    }
  };
  chatWs.onerror = function(){ try{ chatWs.close(); }catch(e){} };
}
function disconnectChat(){
  if(chatWs){ const ws = chatWs; chatWs = null; try{ ws.onclose = null; ws.close(); }catch(e){} }
}
function sendChat(){
  const inp = document.getElementById('chatInput');
  const text = inp.value.trim();
  if(!text) return;
  if(chatWs && chatWs.readyState === WebSocket.OPEN){
    chatWs.send(JSON.stringify({text:text}));
    inp.value = '';
  }else{
    appendChat('<span class="sys">传话失败：通道未连通，稍候重试</span>', 'sys');
  }
}

function enterGame(){ document.getElementById('login').classList.add('hidden');
  document.getElementById('game').classList.remove('hidden');
  document.getElementById('who').textContent = username;
  document.getElementById('log').innerHTML = '';
  document.getElementById('chat').innerHTML = '';
  poll(); loadPeers(); if(window._ti) clearInterval(window._ti);
  window._ti = setInterval(function(){ poll(); loadPeers(); }, 4000);
  connectChat();
}
function doLogout(){
  token=''; username=''; localStorage.removeItem('xx_token'); localStorage.removeItem('xx_user');
  if(window._ti) clearInterval(window._ti);
  disconnectChat();
  showLogin();
}
async function doLogin(){
  const u = document.getElementById('lu').value.trim();
  const p = document.getElementById('lp').value;
  try{
    const res = await api('/api/login', {method:'POST',
      body: JSON.stringify({username:u, password:p})});
    const j = await res.json();
    if(!res.ok){ document.getElementById('msg').textContent = j.detail||'失败'; return; }
    token = j.token; username = j.username;
    localStorage.setItem('xx_token', token); localStorage.setItem('xx_user', username);
    enterGame();
  }catch(e){ document.getElementById('msg').textContent = '网络异常'; }
}
async function doReg(){
  const u = document.getElementById('lu').value.trim();
  const p = document.getElementById('lp').value;
  try{
    const res = await api('/api/register', {method:'POST',
      body: JSON.stringify({username:u, password:p})});
    const j = await res.json();
    if(!res.ok){ document.getElementById('msg').textContent = j.detail||'失败'; return; }
    token = j.token; username = j.username;
    localStorage.setItem('xx_token', token); localStorage.setItem('xx_user', username);
    enterGame();
  }catch(e){ document.getElementById('msg').textContent = '网络异常'; }
}
document.getElementById('btnLogin').onclick = doLogin;
document.getElementById('btnReg').onclick = doReg;
document.getElementById('btnCmd').onclick = function(){ const c=document.getElementById('cmd'); sendCmd(c.value); c.value=''; };
document.getElementById('cmd').addEventListener('keydown', function(e){ if(e.key==='Enter'){ sendCmd(this.value); this.value=''; }});
document.getElementById('btnChat').onclick = sendChat;
document.getElementById('chatInput').addEventListener('keydown', function(e){ if(e.key==='Enter'){ sendChat(); }});
document.getElementById('btnLogout').onclick = async function(){
  try{ await api('/api/logout', {method:'POST'}); }catch(e){}
  doLogout();
};
document.getElementById('btnCloseCat').onclick = closeCatalog;
window.addEventListener('beforeunload', function(){
  if(token) fetch('/api/save', {method:'POST', headers:{'Authorization':'Bearer '+token}, keepalive:true});
});

// 启动：有 token 先尝试进入，否则登录页
(async function(){
  if(token){
    try{ const r = await api('/api/status'); if(r.ok){ enterGame(); return; } }catch(e){}
  }
  showLogin();
})();
</script>
</body>
</html>"""

app = FastAPI(title="联网文字修仙", version="0.1.0")


# ---------- 会话（每账号一个在服游戏实例） ----------
class MpSession:
    def __init__(self, username: str, game) -> None:
        self.username = username
        self.game = game
        self.cli = CLI(game)
        self.last_saved = time.time()
        self.lock = threading.Lock()

    def status(self):
        with self.lock:
            with contextlib.redirect_stdout(io.StringIO()):
                self.cli._settle_online()           # 挂机结算（沉默，不回显）
            data = web_views.player_data(self.game)
            actions = web_views.actions_data(self.game)
            self._maybe_save_locked()
        return data, actions

    def run(self, line: str):
        with self.lock:
            logs = self.cli.run_line(line)          # 内含在线结算 + 派发
            self.game.check_game_over()
            db.save_game(self.game, self.username, note="cmd")
            self.last_saved = time.time()
        return logs

    def save(self) -> None:
        with self.lock:
            db.save_game(self.game, self.username, note="manual")
            self.last_saved = time.time()

    def _maybe_save_locked(self) -> None:
        # 状态轮询频次高，节流到 15s 一次写库，避免无谓落盘
        now = time.time()
        if now - self.last_saved > 15:
            db.save_game(self.game, self.username, note="poll")
            self.last_saved = now


SESSIONS: dict[str, MpSession] = {}


def ensure_session(username: str) -> MpSession:
    s = SESSIONS.get(username)
    if s is not None:
        return s
    try:
        game = db.load_game(username)
    except FileNotFoundError:
        game = create_game(name=username, seed=None)
        db.save_game(game, username, note="new")
    s = MpSession(username, game)
    SESSIONS[username] = s
    world.register(username, s)          # 阶段1a：在服会话进世界状态（在线列表/战力榜）
    return s


# ---------- 鉴权 ----------
async def get_username(req: Request) -> str:
    token = None
    auth = req.headers.get("authorization", "")
    if auth.lower().startswith("bearer "):
        token = auth[7:].strip()
    if not token:
        token = req.query_params.get("token")
    username = db.get_username_by_token(token) if token else None
    if not username:
        raise HTTPException(status_code=401, detail="未登录或登录态已失效")
    return username


# ---------- 请求体 ----------
class AuthReq(BaseModel):
    username: str = ""
    password: str = ""


class CommandReq(BaseModel):
    line: str = ""


# ---------- 路由 ----------
@app.post("/api/register")
async def api_register(req: AuthReq):
    username = req.username.strip()
    if not username or not req.password:
        raise HTTPException(status_code=400, detail="道号与秘钥均不可为空")
    if len(username) > 24 or len(req.password) > 64:
        raise HTTPException(status_code=400, detail="道号≤24字，秘钥≤64字")
    if db.get_account(username):
        raise HTTPException(status_code=409, detail="该道号已被占用")
    pwhash, salt = account.hash_password(req.password)
    token = account.new_token()
    db.create_account(username, pwhash, salt, token)
    game = create_game(name=username, seed=None)
    db.save_game(game, username, note="new")
    return {"token": token, "username": username}


@app.post("/api/login")
async def api_login(req: AuthReq):
    acc = db.get_account(req.username.strip())
    if not acc or not account.verify_password(req.password, acc["salt"], acc["pwhash"]):
        raise HTTPException(status_code=401, detail="道号或秘钥有误")
    token = account.new_token()
    db.set_token(acc["username"], token)
    return {"token": token, "username": acc["username"]}


@app.get("/api/status")
async def api_status(username: str = Depends(get_username)):
    s = ensure_session(username)
    data, actions = s.status()
    return {"data": data, "actions": actions, "user": username}


@app.post("/api/command")
async def api_command(req: CommandReq, username: str = Depends(get_username)):
    s = ensure_session(username)
    logs = s.run(req.line[:200])
    return {"logs": logs, "time": s.game.time_text()}


@app.get("/api/catalog")
async def api_catalog(username: str = Depends(get_username)):
    s = ensure_session(username)
    return {"catalog": web_views.catalog_data(s.game)}


@app.get("/api/online")
async def api_online(username: str = Depends(get_username)):
    # 阶段1a：同服在线列表（按战力倒序）
    ensure_session(username)
    return {"online": world.online_list()}


@app.get("/api/rank")
async def api_rank(username: str = Depends(get_username)):
    # 阶段1a：全服战力榜（在线 + 离线，按综合战力倒序）
    ensure_session(username)
    return {"rank": world.rank_board()}


@app.websocket("/ws/chat")
async def ws_chat(ws: WebSocket, token: str = ""):
    """阶段1b：世界聊天 + 实时上下线广播。

    鉴权走 query 参数 token（浏览器 WebSocket 不支持自定义 header）。
    连接即上线广播，断开即下线广播；收到文本消息广播给全部活连接。
    """
    username = db.get_username_by_token(token) if token else None
    if not username:
        await ws.close(code=4401)
        return
    await ws.accept()
    ensure_session(username)

    first = world.ws_connect(username, ws)
    if first:
        await world.broadcast(
            {"type": "presence", "username": username, "online": True,
             "realm": ensure_session(username).game.player.realm_name},
        )

    try:
        while True:
            data = await ws.receive_json()
            text = str(data.get("text", "")).strip()[:200]
            if not text:
                continue
            sess = world.WORLD.get(username)
            realm = sess.game.player.realm_name if sess else ""
            await world.broadcast({
                "type": "chat", "username": username, "realm": realm, "text": text,
            })
    except WebSocketDisconnect:
        pass
    except Exception:
        pass
    finally:
        last = world.ws_disconnect(username, ws)
        if last:
            await world.broadcast(
                {"type": "presence", "username": username, "online": False},
            )


@app.post("/api/save")
async def api_save(username: str = Depends(get_username)):
    ensure_session(username).save()
    return {"ok": True}


@app.post("/api/logout")
async def api_logout(username: str = Depends(get_username)):
    db.set_token(username, account.new_token())     # 旧 token 失效
    SESSIONS.pop(username, None)
    world.unregister(username)                      # 阶段1a：从世界状态注销
    return {"ok": True}


@app.get("/", response_class=HTMLResponse)
async def index():
    return HTMLResponse(_PAGE)


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8000)
