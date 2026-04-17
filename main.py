from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field
from contextlib import asynccontextmanager
from collections import defaultdict
from datetime import datetime, timezone
from typing import Optional
import asyncio, random, re, time, logging

logging.basicConfig(level=logging.INFO)
TICKERS=["BTC","ETH","SOL","BNB","PEPE","WIF","ARB","OP","SUI","DOGE","AVAX","LINK","TON","TRX","NEAR"]
PRICES={"BTC":68000,"ETH":3500,"SOL":180,"BNB":580,"PEPE":0.000012,"WIF":2.8,"ARB":1.2,"OP":2.5,"SUI":1.8,"DOGE":0.18,"AVAX":38,"LINK":18,"TON":6.5,"TRX":0.12,"NEAR":7.2}
POSTS=["$BTC 剛剛突破70000！🚀🔥","$ETH 機構悄悄佈局，大爆發在即","$SOL DEX成交量創新高💎","$PEPE 社群熱度飆升📈","$WIF 24小時漲幅15%！","$BTC/$ETH 牛市信號確認！","$DOGE 死叉信號，注意回調⚠️","$ARB 重大路線圖更新","$BNB 鏈上活躍地址創新高","$LINK 機構持倉大幅提升","$SUI 能否超越$SOL？","$TON 月活突破1000萬","$NEAR AI敘事加持","$TRX 穩定幣交易量前三"]
WINDOW=300

def extract(text):
    found=set()
    for t in re.findall(r"[\$#]([A-Z]{2,10})",text.upper()):
        if t in TICKERS: found.add(t)
    return list(found)

class S:
    running=False; connected=False
    scan_interval=15; heat_threshold=5; volume_ratio=1.5
    tp_pct=3.0; sl_pct=1.5; order_size=20.0
    ticker_window=defaultdict(list)
    posts=[]; trades=[]; open_positions=[]; logs=[]; alerts=[]
    pnl=0.0; balance=1000.0; ws_clients=[]; scan_task=None; pos_task=None; scan_count=0; heat_data=[]

    @classmethod
    def log(cls,msg):
        e=f"[{datetime.now().strftime('%H:%M:%S')}] {msg}"
        cls.logs.insert(0,e); cls.logs=cls.logs[:300]

    @classmethod
    def alert(cls,msg,kind="info"):
        cls.alerts.insert(0,{"id":time.time()+random.random(),"msg":msg,"type":kind,"time":datetime.now().strftime("%H:%M:%S")})
        cls.alerts=cls.alerts[:50]

def upd_win(counts):
    now=time.time()
    for t,c in counts.items(): S.ticker_window[t].append((now,c))
    cut=now-WINDOW
    for t in list(S.ticker_window): S.ticker_window[t]=[(ts,c) for ts,c in S.ticker_window[t] if ts>cut]

def get_total(t): return sum(c for _,c in S.ticker_window.get(t,[]))
def get_hot(): return [t for t in S.ticker_window if get_total(t)>=S.heat_threshold]

async def broadcast(event,data):
    dead=[]
    for ws in S.ws_clients:
        try: await ws.send_json({"event":event,"data":data})
        except: dead.append(ws)
    for ws in dead:
        if ws in S.ws_clients: S.ws_clients.remove(ws)

def make_posts(n=8):
    hot=random.choice(TICKERS) if random.random()<0.45 else None
    posts=[]
    for _ in range(n):
        text=random.choice(POSTS)
        if hot and random.random()<0.5: text=f"${hot} "+text
        tickers=extract(text) or [random.choice(TICKERS)]
        posts.append({"id":f"{int(time.time()*1000)}{random.randint(0,9999)}","text":text,"tickers":tickers,"likes":random.randint(5,800),"time":datetime.now().strftime("%H:%M:%S"),"sentiment":round(random.uniform(-1,1),3)})
    return posts

async def do_trade(ticker,vr):
    price=PRICES.get(ticker,1.0)*random.uniform(0.97,1.03)
    qty=round(S.order_size/price,6)
    tp=round(price*(1+S.tp_pct/100),8); sl=round(price*(1-S.sl_pct/100),8)
    trade={"id":f"SIM-{int(time.time())}-{ticker}","ticker":ticker,"side":"BUY","price":price,"qty":qty,"total":round(price*qty,4),"tp":tp,"sl":sl,"vol_ratio":vr,"status":"FILLED","time":datetime.now().strftime("%H:%M:%S"),"open":True}
    S.trades.insert(0,trade); S.trades=S.trades[:200]; S.open_positions.append(trade); S.balance-=S.order_size
    S.log(f"✅ BUY {ticker} @ {price:.6f} | TP:{tp:.6f} | SL:{sl:.6f}"); S.alert(f"BUY {ticker} 成交","success")
    await broadcast("new_trade",trade)

async def full_state():
    return {"running":S.running,"connected":S.connected,"pnl":round(S.pnl,4),"balance":round(S.balance,4),"logs":S.logs[:30],"alerts":S.alerts[:15],"trades":S.trades[:20],"posts":S.posts[:10],"open_positions":S.open_positions,"heat_data":S.heat_data,"scan_count":S.scan_count}

async def run_scan():
    S.scan_count+=1; S.log(f"↻ 掃描 #{S.scan_count}")
    posts=make_posts(random.randint(6,14)); S.posts=posts+S.posts; S.posts=S.posts[:100]
    counts=defaultdict(int)
    for p in posts:
        for t in p["tickers"]: counts[t]+=random.randint(1,3)
    upd_win(dict(counts))
    heat=[]
    for ticker in TICKERS:
        m=get_total(ticker); vr=round(random.uniform(1.4,3.5) if m>=S.heat_threshold else random.uniform(0.4,1.4),2)
        heat.append({"ticker":ticker,"mentions":m,"sentiment":round(random.uniform(-1,1),3),"price":round(PRICES.get(ticker,1)*random.uniform(0.97,1.03),8),"priceChange":round(random.uniform(-8,8),2),"volumeRatio":vr,"isHot":m>=S.heat_threshold,"isSignal":m>=S.heat_threshold and vr>=S.volume_ratio})
    heat.sort(key=lambda x:x["mentions"],reverse=True); S.heat_data=heat
    for ticker in get_hot():
        m=get_total(ticker); vr=next((d["volumeRatio"] for d in heat if d["ticker"]==ticker),1.0)
        S.log(f"🔥 {ticker} ({m}次) | {vr:.2f}x")
        if vr>=S.volume_ratio:
            if any(p["ticker"]==ticker for p in S.open_positions): continue
            if S.balance<S.order_size: S.alert("餘額不足","error"); continue
            await do_trade(ticker,vr)
    await broadcast("state_update",await full_state())

async def scan_loop():
    S.log("🤖 啟動")
    while S.running:
        try: await run_scan()
        except Exception as e: S.log(f"❌ {e}")
        await asyncio.sleep(S.scan_interval)

async def pos_monitor():
    while S.running:
        await asyncio.sleep(8)
        closed=[]
        for pos in S.open_positions:
            cur=PRICES.get(pos["ticker"],1)*random.uniform(0.97,1.03)
            if cur>=pos["tp"] or cur<=pos["sl"]:
                result="止盈" if cur>=pos["tp"] else "止損"
                pnl=(cur-pos["price"])*pos["qty"]; S.pnl+=pnl; S.balance+=S.order_size+pnl
                pos.update({"open":False,"close_price":cur,"pnl":round(pnl,4)})
                S.log(f"{'✅' if cur>=pos['tp'] else '🔴'} [{result}] {pos['ticker']} PnL:{pnl:+.4f}")
                S.alert(f"{pos['ticker']} {result} {pnl:+.4f}U","success" if cur>=pos["tp"] else "error")
                closed.append(pos)
                await broadcast("position_closed",{"ticker":pos["ticker"],"result":result,"pnl":pnl})
        for p in closed:
            if p in S.open_positions: S.open_positions.remove(p)
        if closed: await broadcast("state_update",await full_state())

@asynccontextmanager
async def lifespan(app):
    S.log("[系統] SQBOT 啟動"); yield
    if S.scan_task: S.scan_task.cancel()
    if S.pos_task: S.pos_task.cancel()

app=FastAPI(lifespan=lifespan)
app.add_middleware(CORSMiddleware,allow_origins=["*"],allow_methods=["*"],allow_headers=["*"])

@app.get("/",response_class=HTMLResponse)
async def index():
    try: return open("index.html",encoding="utf-8").read()
    except: return "<h1>SQBOT Running</h1><p>index.html not found</p>"

class CR(BaseModel): api_key:str="TEST"; api_secret:str="TEST"
class CF(BaseModel):
    scan_interval:int=Field(15,ge=5,le=300); heat_threshold:int=Field(5,ge=1,le=50)
    volume_ratio:float=Field(1.5,ge=1.0,le=10.0); tp_pct:float=Field(3.0,ge=0.1,le=50.0)
    sl_pct:float=Field(1.5,ge=0.1,le=20.0); order_size:float=Field(20.0,ge=1.0,le=500.0)

@app.post("/api/connect")
async def connect(r:CR): S.connected=True; S.log("✓ 連接成功"); S.alert("連接成功","success"); return {"success":True,"balance":S.balance}

@app.post("/api/disconnect")
async def disconnect():
    S.running=False; S.connected=False
    if S.scan_task: S.scan_task.cancel(); S.scan_task=None
    if S.pos_task: S.pos_task.cancel(); S.pos_task=None
    return {"success":True}

@app.post("/api/bot/start")
async def start():
    from fastapi import HTTPException
    if not S.connected: raise HTTPException(400,"請先連接")
    if S.running: raise HTTPException(400,"已在運行")
    S.running=True; S.scan_task=asyncio.create_task(scan_loop()); S.pos_task=asyncio.create_task(pos_monitor())
    S.alert("機器人已啟動","success"); return {"success":True}

@app.post("/api/bot/stop")
async def stop():
    S.running=False
    if S.scan_task: S.scan_task.cancel(); S.scan_task=None
    if S.pos_task: S.pos_task.cancel(); S.pos_task=None
    S.alert("機器人已停止","info"); return {"success":True}

@app.put("/api/bot/config")
async def config(c:CF):
    S.scan_interval=c.scan_interval; S.heat_threshold=c.heat_threshold; S.volume_ratio=c.volume_ratio
    S.tp_pct=c.tp_pct; S.sl_pct=c.sl_pct; S.order_size=c.order_size; return {"success":True}

@app.get("/api/status")
async def status(): return {"running":S.running,"connected":S.connected,"pnl":S.pnl,"balance":S.balance,"scan_count":S.scan_count,"heat_data":S.heat_data}

@app.get("/api/trades"); async def trades(): return {"trades":S.trades[:50]}
@app.get("/api/positions"); async def positions(): return {"positions":S.open_positions}
@app.get("/api/logs"); async def logs(): return {"logs":S.logs[:100]}
@app.get("/api/alerts"); async def alerts(): return {"alerts":S.alerts[:30]}
@app.get("/api/posts"); async def posts(): return {"posts":S.posts[:20]}
@app.get("/api/heat"); async def heat(): return {"heat":S.heat_data}
@app.get("/api/balance"); async def balance(): return {"USDT":{"free":round(S.balance,4),"total":round(S.balance,4)}}

@app.post("/api/scan/once")
async def scan_once(): await run_scan(); return {"success":True}

@app.post("/api/reset")
async def reset():
    S.running=False
    if S.scan_task: S.scan_task.cancel(); S.scan_task=None
    if S.pos_task: S.pos_task.cancel(); S.pos_task=None
    S.connected=False; S.ticker_window.clear(); S.posts.clear(); S.trades.clear()
    S.open_positions.clear(); S.logs.clear(); S.alerts.clear()
    S.pnl=0.0; S.balance=1000.0; S.scan_count=0; S.heat_data=[]
    S.log("[系統] 已重置"); return {"success":True}

@app.websocket("/ws")
async def ws_ep(websocket:WebSocket):
    await websocket.accept(); S.ws_clients.append(websocket)
    await websocket.send_json({"event":"init","data":await full_state()})
    try:
        while True:
            msg=await websocket.receive_text()
            if msg=="ping": await websocket.send_text("pong")
    except WebSocketDisconnect:
        if websocket in S.ws_clients: S.ws_clients.remove(websocket)

@app.get("/health")
async def health(): return {"status":"ok","time":datetime.now(timezone.utc).isoformat()}
