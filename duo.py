#!/usr/bin/env python3
"""duo.py - paper and live side by side behind one dashboard."""

from __future__ import annotations

import argparse
import json
import os
import signal
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import envfile

PAPER_PORT = 8081
LIVE_PORT = 8082
DASH_PORT = 8080
PROCS: dict[str, subprocess.Popen] = {}


def proxy(port: int, path: str, method: str = "GET"):
    try:
        req = urllib.request.Request(f"http://127.0.0.1:{port}{path}", method=method)
        with urllib.request.urlopen(req, timeout=8) as r:
            return r.status, r.read()
    except urllib.error.HTTPError as e:
        return e.code, e.read()
    except Exception as exc:
        return 502, json.dumps({"error": str(exc)}).encode()


def spawn(side: str, port: int, args) -> None:
    mode = "paper" if side == "paper" else "live"
    eq = args.paper_equity if side == "paper" else args.live_equity
    venues = args.paper_venues if side == "paper" else args.live_venues
    cmd = [sys.executable, "quantbot.py", "--label", side.upper(), "--mode", mode,
           "--db", os.path.join("data", f"{side}.db"), "--port", str(port),
           "--symbols", args.symbols, "--timeframe", args.timeframe,
           "--equity", str(eq), "--risk", str(args.risk),
           "--min-score", str(args.min_score), "--venues", venues]
    tok = os.getenv("QB_DASH_TOKEN", "")
    if tok:
        cmd += ["--token", tok]

    os.makedirs("logs", exist_ok=True)
    log = open(os.path.join("logs", f"{side}.log"), "a", buffering=1)
    PROCS[side] = subprocess.Popen(cmd, stdout=log, stderr=subprocess.STDOUT)
    print(f"  started {side} :{port} equity={eq}")


HTML = """<!doctype html><meta charset=utf-8><title>quantbot dual</title>
<style>
:root{
  --bg:#0b0d11; --card:#11151c; --line:#1e2531; --t:#cfd7e3; --d:#6a7788;
  --green:#3ddc84; --red:#e8455e; --up:#7fe3a1; --dn:#ff8b8b;
}
*{box-sizing:border-box}
html,body{height:100%}
body{margin:0;background:var(--bg);color:var(--t);
  font:13px/1.45 ui-monospace,"Cascadia Mono",Consolas,monospace;padding:20px 22px 28px}
.top{display:flex;justify-content:space-between;align-items:baseline;margin-bottom:18px}
.brand{font-size:11px;letter-spacing:.22em;color:#8b98a8}
.combo{font-size:11px;color:var(--d)}
.cols{display:grid;grid-template-columns:1fr 1fr;gap:14px;align-items:start}
.col{display:flex;flex-direction:column;gap:12px;min-width:0}
.card{border-radius:6px;padding:16px 18px 12px}
.card.paper{background:var(--green);color:#06210f}
.card.live{background:var(--red);color:#fff}
.chead{display:flex;justify-content:space-between;align-items:center;margin-bottom:14px}
.ctitle{font-size:clamp(10px,.9vw,12px);letter-spacing:.14em;font-weight:700;
  white-space:nowrap}
.card.paper .ctitle{color:#0a3318}
.card.live .ctitle{color:#fff}
.pill{font-size:9px;letter-spacing:.14em;padding:3px 9px;border-radius:20px;
  background:rgba(0,0,0,.22);color:#fff}
.card.paper .pill{color:#06210f;background:rgba(255,255,255,.42)}
.bl{font-size:9px;letter-spacing:.16em;opacity:.72;margin-bottom:1px}
.bv{font-size:clamp(21px,3.4vw,34px);font-weight:600;letter-spacing:-.02em;line-height:1.15;
  font-variant-numeric:tabular-nums;margin-bottom:10px;white-space:nowrap}
.r{display:flex;justify-content:space-between;align-items:baseline;
  padding:6px 0;border-top:1px solid rgba(0,0,0,.16)}
.card.live .r{border-top-color:rgba(255,255,255,.22)}
.r span{font-size:9.5px;letter-spacing:.14em;opacity:.8}
.r b{font-size:14px;font-weight:600;font-variant-numeric:tabular-nums}
.cpos{font-size:10.5px;opacity:.82;padding-top:9px;line-height:1.5}
.detail{background:var(--card);border:1px solid var(--line);border-radius:6px;padding:12px 14px}
.dhead{display:flex;justify-content:space-between;align-items:center;margin-bottom:8px}
.dl{font-size:9px;letter-spacing:.16em;color:var(--d)}
select{background:#0b0e13;color:var(--t);border:1px solid #29313f;border-radius:4px;
  padding:3px 7px;font:inherit;font-size:10px}
canvas{width:100%;height:64px;display:block}
.stats{display:flex;gap:16px;font-size:10px;color:#93a0b1;
  border-top:1px solid #ffffff10;margin-top:9px;padding-top:8px}
.stats b{color:var(--t);font-weight:600;font-variant-numeric:tabular-nums}
.trades{font-size:10px;color:#93a0b1;border-top:1px solid #ffffff10;margin-top:8px;
  padding-top:8px;line-height:1.75;min-height:16px}
.btns{margin-top:10px;display:flex;gap:6px}
button{background:none;border:1px solid #2b3543;color:var(--t);padding:4px 11px;
  border-radius:4px;cursor:pointer;font:inherit;font-size:10px;letter-spacing:.08em}
button:hover{border-color:#3c4a5c;color:#fff}
button:disabled{opacity:.35;cursor:default}
button:disabled:hover{border-color:#2b3543;color:var(--t)}
.hide{display:none}
.up{color:var(--up)}.dn{color:var(--dn)}
.card .up{color:inherit}.card .dn{color:inherit}
@media(max-width:520px){.cols{grid-template-columns:1fr}}
</style>
<div class="top"><div class="brand">QUANTBOT // DUAL BOOK</div><div class="combo" id="combo">-</div></div>
<div class="cols">
  <div class="col">
    <div class="card paper">
      <div class="chead"><div class="ctitle">PAPER &middot; VIRTUAL</div>
        <div class="pill" id="s_paper">...</div></div>
      <div class="bl">EQUITY</div><div class="bv" id="e_paper">-</div>
      <div class="r"><span>DAY P&amp;L</span><b id="d_paper">-</b></div>
      <div class="r"><span>OPEN P&amp;L</span><b id="o_paper">-</b></div>
      <div class="r"><span>EXPOSURE</span><b id="z_paper">-</b></div>
      <div class="cpos" id="x_paper">no open positions</div>
    </div>
    <div class="detail">
      <div class="dhead"><span class="dl">EQUITY CURVE</span>
        <select id="v_paper" onchange="setVen('paper',this.value)"></select></div>
      <canvas id="c_paper" width="560" height="64"></canvas>
      <div class="stats" id="k_paper"></div>
      <div class="trades" id="t_paper">no trades yet</div>
      <div class="btns" id="b_paper"><button id="pb_paper" data-k="pause"
        onclick="act('paper',this.dataset.k)">PAUSE</button>
        <button onclick="act('paper','flatten')">FLATTEN</button></div>
    </div>
  </div>
  <div class="col">
    <div class="card live">
      <div class="chead"><div class="ctitle">LIVE &middot; REAL MONEY</div>
        <div class="pill" id="s_live">...</div></div>
      <div class="bl">EQUITY</div><div class="bv" id="e_live">-</div>
      <div class="r"><span>DAY P&amp;L</span><b id="d_live">-</b></div>
      <div class="r"><span>OPEN P&amp;L</span><b id="o_live">-</b></div>
      <div class="r"><span>EXPOSURE</span><b id="z_live">-</b></div>
      <div class="cpos" id="x_live">no open positions</div>
    </div>
    <div class="detail">
      <div class="dhead"><span class="dl">EQUITY CURVE</span>
        <select id="v_live" onchange="setVen('live',this.value)"></select></div>
      <canvas id="c_live" width="560" height="64"></canvas>
      <div class="stats" id="k_live"></div>
      <div class="trades" id="t_live">no trades yet</div>
      <div class="btns" id="b_live"><button id="pb_live" data-k="pause"
        onclick="act('live',this.dataset.k)">PAUSE</button>
        <button onclick="act('live','flatten')">FLATTEN</button></div>
    </div>
  </div>
</div>
<script>
const T=new URLSearchParams(location.search).get('t')||'';
const Q=T?'?t='+encodeURIComponent(T):'';
if (new URLSearchParams(location.search).has('lock')){
  b_paper.classList.add('hide'); b_live.classList.add('hide');
  v_paper.classList.add('hide'); v_live.classList.add('hide');
}
const money=v=>(v<0?'-$':'$')+Math.abs(v).toFixed(2);
const signed=v=>(v>=0?'+':'-')+'$'+Math.abs(v).toFixed(2);
async function act(side,k){ await fetch('/api/'+side+'/'+k+Q,{method:'POST'}); load(); }
const setVen=(side,v)=>fetch('/api/'+side+'/venue?v='+encodeURIComponent(v)+
  (T?'&t='+encodeURIComponent(T):''),{method:'POST'}).then(load);
function draw(cv,pts){
  const c=cv.getContext('2d'), W=cv.width, H=cv.height;
  c.clearRect(0,0,W,H);
  if(pts.length<2){ c.fillStyle='#5d6b80'; c.font='10px ui-monospace,monospace';
    c.fillText('collecting samples - one every 30s',4,H/2+3); return; }
  const lo=Math.min(...pts.map(p=>p.equity)), hi=Math.max(...pts.map(p=>p.equity)), span=(hi-lo)||1;
  c.beginPath();
  const flat=(hi-lo)<0.005;
  pts.forEach((p,i)=>{ const x=i/(pts.length-1)*W,
    y=flat?H/2:H-7-((p.equity-lo)/span)*(H-16);
    i?c.lineTo(x,y):c.moveTo(x,y); });
  c.strokeStyle=pts[pts.length-1].equity>=pts[0].equity?'#3ddc84':'#e8455e';
  c.lineWidth=1.4; c.stroke();
  c.fillStyle='#6a7788'; c.font='9px ui-monospace,monospace'; c.textAlign='right';
  if(!flat){ c.fillText('$'+hi.toFixed(0),W-2,10);
                  c.fillText('$'+lo.toFixed(0),W-2,H-2); }
  else c.fillText('$'+hi.toFixed(2),W-2,10);
  c.textAlign='left';
}
async function extras(side){
  try{ const r=await fetch('/api/'+side+'/equity'+Q);
       if(r.ok) draw(document.getElementById('c_'+side), await r.json()); }catch(e){}
  try{ const r=await fetch('/api/'+side+'/events'+Q);
       if(r.ok){
         const rows=(await r.json()).slice(0,4);
         document.getElementById('t_'+side).innerHTML = rows.length ? rows.map(e=>
           (e.kind==='open'?'OPEN':'EXIT')+' '+(e.side||'')+' '+(e.symbol||'')+' '+
           (e.qty?Number(e.qty).toFixed(4):'')+' @ '+(e.price?Number(e.price).toFixed(2):'')+
           (e.pnl!=null?' <span class="'+(e.pnl>=0?'up':'dn')+'">'+signed(Number(e.pnl))+'</span>':'')
         ).join('<br>') : 'no trades yet';
       } }catch(e){}
}
const alive={paper:true, live:true};
let tick=0;
async function one(side){
  let d;
  try{ const r=await fetch('/api/'+side+'/status'+Q); if(!r.ok) throw 0; d=await r.json(); }
  catch{ alive[side]=false;
    document.getElementById('s_'+side).textContent='OFFLINE';
    document.getElementById('e_'+side).textContent='-';
    document.querySelectorAll('#b_'+side+' button').forEach(b=>b.disabled=true);
    return null; }
  alive[side]=true;
  const se=document.getElementById('s_'+side);
  se.textContent=d.halted?'HALTED':d.paused?'PAUSED':'RUNNING';
  document.getElementById('e_'+side).textContent=money(d.equity);
  document.getElementById('d_'+side).textContent=signed(d.day_pnl);
  const open=d.positions.reduce((s,p)=>s+(p.pnl||0),0);
  document.getElementById('o_'+side).textContent=signed(open);
  document.getElementById('z_'+side).textContent=(d.exposure_pct||0).toFixed(1)+'%';
  document.getElementById('x_'+side).textContent=d.positions.length
    ? d.positions.map(p=>p.symbol+' '+p.side+' '+p.qty+' - '+signed(p.pnl)).join('   ')
    : 'no open positions';
  document.getElementById('k_'+side).innerHTML =
    '<span>TRADES <b>'+d.stats.trades+'</b></span>'+
    '<span>WIN RATE <b>'+d.stats.win_rate.toFixed(0)+'%</b></span>'+
    '<span>REALIZED <b>'+signed(d.stats.realized)+'</b></span>'+
    '<span>VENUE <b>'+d.active_venue+'</b></span>';
  // one button for both directions: pausing must be undoable from here
  const pb=document.getElementById('pb_'+side), away=d.paused||d.halted;
  pb.textContent=away?'RESUME':'PAUSE';
  pb.dataset.k=away?'resume':'pause';
  document.querySelectorAll('#b_'+side+' button').forEach(b=>b.disabled=false);
  const sel=document.getElementById('v_'+side);
  if(sel.options.length!==d.venues.length+1){
    sel.innerHTML='<option value="auto">auto</option>'+
      d.venues.map(v=>'<option value="'+v+'">'+v+'</option>').join('');
  }
  sel.value=d.active_venue;
  await extras(side);
  return d;
}
async function load(){
  tick++;
  const jobs=[one('paper')];
  /* a panel that is offline is re-checked every ~20s instead of every 2s,
     otherwise a stopped live engine fills the console with 502s */
  if(alive.live || tick%10===0) jobs.push(one('live'));
  const got=(await Promise.all(jobs)).filter(Boolean);
  if(got.length){ const t=got.reduce((s,d)=>s+(d.day_pnl||0),0);
    combo.textContent='combined day '+signed(t)+(got.length<2?' - paper only':''); }
}
load(); setInterval(load,2000);
</script>"""


def make_handler(args):
    class H(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def _send(self, code, body, ctype="application/json"):
            b = body.encode() if isinstance(body, str) else body
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(b)))
            self.end_headers()
            self.wfile.write(b)

        def do_GET(self):
            u = urllib.parse.urlparse(self.path)
            if u.path == "/":
                return self._send(200, HTML, "text/html; charset=utf-8")
            for side, port in (("paper", PAPER_PORT), ("live", LIVE_PORT)):
                if u.path.startswith(f"/api/{side}/"):
                    # the engine serves /api/status, /api/pause ... so keep the
                    # /api prefix that identifies the side
                    rest = "/api" + u.path[len(f"/api/{side}"):]
                    q = "?t=" + os.getenv("QB_DASH_TOKEN", "")
                    if u.query:
                        q += "&" + u.query
                    code, body = proxy(port, rest + q)
                    return self._send(code, body)
            self._send(404, '{"error":"not found"}')

        def do_POST(self):
            u = urllib.parse.urlparse(self.path)
            for side, port in (("paper", PAPER_PORT), ("live", LIVE_PORT)):
                if u.path.startswith(f"/api/{side}/"):
                    rest = "/api" + u.path[len(f"/api/{side}"):]
                    q = "?t=" + os.getenv("QB_DASH_TOKEN", "")
                    if u.query:
                        q += "&" + u.query
                    code, body = proxy(port, rest + q, "POST")
                    return self._send(code, body)
            self._send(404, '{"error":"not found"}')

    return H


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--live", action="store_true")
    p.add_argument("--lock", action="store_true")
    p.add_argument("--symbols", default="BTC/USDT,ETH/USDT")
    p.add_argument("--timeframe", default="5m")
    p.add_argument("--risk", type=float, default=0.5)
    p.add_argument("--min-score", type=float, default=0.65)
    p.add_argument("--paper-equity", type=float, default=10_000.0)
    p.add_argument("--live-equity", type=float, default=500.0)
    p.add_argument("--paper-venues", default="binance")
    p.add_argument("--live-venues", default="binance")
    a = p.parse_args()
    # children inherit the environment, so one load covers both engines
    envfile.load()

    os.makedirs("data", exist_ok=True)
    os.makedirs("logs", exist_ok=True)

    print("\n  quantbot dual")
    spawn("paper", PAPER_PORT, a)
    if a.live:
        spawn("live", LIVE_PORT, a)
    else:
        print("  live skipped (--live to enable)")

    time.sleep(2.5)
    srv = ThreadingHTTPServer(("127.0.0.1", DASH_PORT), make_handler(a))
    print(f"\n  dashboard http://127.0.0.1:{DASH_PORT}{'?lock' if a.lock else ''}\n  ctrl-c to stop\n")

    def shutdown(*_):
        for name, pr in PROCS.items():
            pr.terminate()
        srv.shutdown()
        sys.exit(0)

    signal.signal(signal.SIGINT, shutdown)
    try:
        srv.serve_forever()
    finally:
        shutdown()


if __name__ == "__main__":
    sys.exit(main())