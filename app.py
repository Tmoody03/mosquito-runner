"""Production API for Mosquito Runner; paper-safe by default."""
from __future__ import annotations
import concurrent.futures,hmac,json,math,os,re,tempfile,threading,time
from collections import defaultdict,deque
from datetime import date,datetime,timedelta,timezone
from functools import wraps
from pathlib import Path
from flask import Flask,jsonify,make_response,request,send_from_directory,redirect
from strategy import AI_UNIVERSE,build_watchlist,rank_watchlist
import simulator
import engine
from protection import protected_state
from lifecycle import Lifecycle
from scheduler import MarketScheduler

app=Flask(__name__,static_folder=None); app.config["MAX_CONTENT_LENGTH"]=65536
LOCK=threading.RLock(); EXIT_LOCK=threading.Lock(); CACHE={}; CALLS=defaultdict(deque); EXIT_RESULTS={}
ENGINE_THREAD=None;ENGINE_THREAD_LOCK=threading.Lock();SCHEDULER_THREAD=None;SCHEDULER_THREAD_LOCK=threading.Lock();RUNTIME_STOP=threading.Event()
RESET_THREAD=None;RESET_THREAD_LOCK=threading.Lock()
BROKER_HEALTH_LOCK=threading.Lock();BROKER_HEALTH_FUTURE=None;BROKER_HEALTH_CACHE=None;BROKER_HEALTH_EXPIRES=0.0
BROKER_HEALTH_POOL=concurrent.futures.ThreadPoolExecutor(max_workers=1,thread_name_prefix="broker-health")
STATE_PATH=Path(os.getenv("MOSQUITO_STATE_FILE","/tmp/mosquito-runner-state.json"))
DEFAULTS={"allocation":0.0,"requested_investment":0.0,"baseline_equity":None,"daily_goal":500.0,"running":False,"armed":True,"exit_status":None,"exit_request_id":None,"last_scan":None,"last_error":None,"updated_at":None}
def now(): return datetime.now(timezone.utc).isoformat()
def truthy(name): return os.getenv(name,"").lower() in {"1","true","yes","on"}
def paper(): return True
def enabled(): return truthy("ALPACA_ENABLE_ORDER_EXECUTION")
def _safe_log_text(value):
    text=str(value or "")[:300].replace("\r"," ").replace("\n"," ")
    for name in ("ALPACA_API_KEY","ALPACA_API_KEY_ID","APCA_API_KEY_ID","ALPACA_SECRET_KEY","ALPACA_API_SECRET_KEY","APCA_API_SECRET_KEY"):
        secret=os.getenv(name)
        if secret:text=text.replace(secret,"[REDACTED]")
    text=re.sub(r"(?i)(authorization|api[-_ ]?key|secret|token)(\s*[:=]\s*)([^,; ]+)",r"\1\2[REDACTED]",text)
    return text
def log_broker_error(exc,operation,endpoint=None):
    fields={"error_class":type(exc).__name__,"operation":operation}
    if endpoint:fields["endpoint"]=endpoint
    try:
        from alpaca.common.exceptions import APIError
        if isinstance(exc,APIError):
            for name in ("status_code","code","message"):
                try:value=getattr(exc,name,None)
                except Exception:value=None
                if value not in (None,""):fields[name]=_safe_log_text(value)
    except ImportError:pass
    app.logger.warning("broker failure %s",json.dumps(fields,separators=(",",":"),sort_keys=True))
def engine_loop():
    while True:
        try:
            if load_state().get("running"):
                broker=client();life=lifecycle_for(broker);life.reconcile();engine.cycle(broker,submit_enabled=enabled());reconcile_closed_positions(broker,life)
        except Exception as exc:log_broker_error(exc,"trailing_engine_cycle")
        if RUNTIME_STOP.wait(max(2,number(os.getenv("MOSQUITO_ENGINE_INTERVAL")) or 5)):break
def ensure_engine():
    global ENGINE_THREAD
    with ENGINE_THREAD_LOCK:
        if ENGINE_THREAD is None or not ENGINE_THREAD.is_alive():
            ENGINE_THREAD=threading.Thread(target=engine_loop,name="mosquito-trailing-engine",daemon=True);ENGINE_THREAD.start()
def credentials():
    return (os.getenv("ALPACA_API_KEY") or os.getenv("ALPACA_API_KEY_ID") or os.getenv("APCA_API_KEY_ID"),os.getenv("ALPACA_SECRET_KEY") or os.getenv("ALPACA_API_SECRET_KEY") or os.getenv("APCA_API_SECRET_KEY"))
def client():
    key,secret=credentials()
    if not key or not secret: raise RuntimeError("missing credentials")
    from alpaca.trading.client import TradingClient
    return TradingClient(key,secret,paper=paper())
def _broker_health_probe():
    broker=client();broker.get_account();broker.get_clock()
    return True
def broker_health_data():
    global BROKER_HEALTH_FUTURE,BROKER_HEALTH_CACHE,BROKER_HEALTH_EXPIRES
    checked_at=now();key,secret=credentials()
    if not key or not secret:return {"status":"degraded","configured":False,"authenticated":False,"account_readable":False,"clock_readable":False,"paper_mode":paper(),"checked_at":checked_at}
    with BROKER_HEALTH_LOCK:
        if BROKER_HEALTH_CACHE is not None and BROKER_HEALTH_EXPIRES>time.monotonic():return dict(BROKER_HEALTH_CACHE)
        if BROKER_HEALTH_FUTURE is None or BROKER_HEALTH_FUTURE.done():BROKER_HEALTH_FUTURE=BROKER_HEALTH_POOL.submit(_broker_health_probe)
        future=BROKER_HEALTH_FUTURE
    timeout=max(.25,min(5.0,number(os.getenv("MOSQUITO_BROKER_HEALTH_TIMEOUT")) or 2.0))
    try:
        future.result(timeout=timeout)
        result={"status":"ok","configured":True,"authenticated":True,"account_readable":True,"clock_readable":True,"paper_mode":paper(),"checked_at":checked_at}
        ttl=30.0
    except concurrent.futures.TimeoutError:
        result={"status":"degraded","configured":True,"authenticated":False,"account_readable":False,"clock_readable":False,"paper_mode":paper(),"checked_at":checked_at}
        ttl=5.0
    except Exception as exc:
        log_broker_error(exc,"broker_health_probe","/v2/account,/v2/clock")
        result={"status":"degraded","configured":True,"authenticated":False,"account_readable":False,"clock_readable":False,"paper_mode":paper(),"checked_at":checked_at}
        ttl=30.0
    with BROKER_HEALTH_LOCK:
        BROKER_HEALTH_CACHE=dict(result);BROKER_HEALTH_EXPIRES=time.monotonic()+ttl
        if future.done():BROKER_HEALTH_FUTURE=None
    return result
class PaperBrokerAdapter:
    paper=True
    def __init__(self,broker):self.broker=broker
    def submit_order(self,request):
        if not enabled():raise RuntimeError("broker order execution is disabled")
        from alpaca.trading.enums import OrderSide,OrderType,TimeInForce
        from alpaca.trading.requests import MarketOrderRequest
        if str(request.get("side")).lower()!="buy":raise RuntimeError("paper lifecycle accepts buy orders only")
        order=MarketOrderRequest(symbol=request["symbol"],notional=float(request["notional"]),side=OrderSide.BUY,type=OrderType.MARKET,time_in_force=TimeInForce.DAY,client_order_id=request["client_order_id"])
        return self.broker.submit_order(order_data=order)
    def get_order_by_client_id(self,client_id):
        try:return self.broker.get_order_by_client_id(client_id)
        except Exception:return None
class BrokerClock:
    def __init__(self,broker):self.broker=broker
    def now(self):return getattr(self.broker.get_clock(),"timestamp",None) or datetime.now(timezone.utc)
    def is_market_open(self):return bool(getattr(self.broker.get_clock(),"is_open",False))
def lifecycle_for(broker):
    minutes=max(0,number(os.getenv("MOSQUITO_REBUY_COOLDOWN_MINUTES")) or 5)
    return Lifecycle(PaperBrokerAdapter(broker),os.getenv("MOSQUITO_LIFECYCLE_FILE","/data/mosquito-lifecycle.json"),BrokerClock(broker),portfolio_size=50,rebuy_cooldown=timedelta(minutes=minutes))
def reconcile_closed_positions(broker,life):
    data=engine.load();changed=False;closed=[]
    for event in data.get("events",[]):
        if event.get("type")!="POSITION_CLOSED_PENDING_RECONCILIATION" or event.get("reconciled"):continue
        order_id=event.get("order_id")
        if not order_id:continue
        try:order=broker.get_order_by_id(order_id)
        except Exception:continue
        status=str(getattr(order,"status","")).lower();price=number(getattr(order,"filled_avg_price",None));qty=number(getattr(order,"filled_qty",None))
        if status=="filled" and price and qty:
            life.record_exit(event["symbol"],fill_price=price,filled_at=getattr(order,"filled_at",None));event.update(reconciled=True,sell_price=price,filled_qty=qty,reconciled_at=now());closed.append(event["symbol"]);changed=True
    if changed:
        engine.save(data)
        try:
            picks=confirmed_entry_picks(tradable_picks(broker,60));prices={p["ticker"]:p["price"] for p in picks};bp=number(getattr(broker.get_account(),"buying_power",0)) or 0
            life.rebalance(picks,prices,bp,dead_symbols=())
        except Exception as exc:log_broker_error(exc,"replacement_cycle")
    return closed
def tradable_picks(broker,count=50):
    from alpaca.trading.enums import AssetClass,AssetStatus
    from alpaca.trading.requests import GetAssetsRequest
    assets=broker.get_all_assets(GetAssetsRequest(status=AssetStatus.ACTIVE,asset_class=AssetClass.US_EQUITY))
    tradable={str(getattr(a,"symbol","")).upper() for a in assets if bool(getattr(a,"tradable",False)) and bool(getattr(a,"fractionable",False))}
    candidates=[symbol for symbol in AI_UNIVERSE if symbol in tradable];close,volume=alpaca_history(candidates);watch=rank_watchlist(close,volume,min(250,200+count),source="alpaca_daily_bars")
    picks=[dict(p) for p in watch.get("picks",[]) if p.get("ticker") in tradable]
    if len(picks)<count:raise RuntimeError(f"Only {len(picks)} eligible Alpaca-tradable V5.8 names were available")
    for rank,row in enumerate(picks,1):row.update(rank=rank,eligible=True)
    return picks
def entry_signal_met(session_open,current_price,threshold=0.005):
    """Return true only after a stock gains the required amount from today's open."""
    opened=number(session_open);current=number(current_price);threshold=number(threshold)
    return bool(opened and opened>0 and current and current>0 and threshold is not None and threshold>=0 and current>=opened*(1+threshold))
def confirmed_entry_picks(picks):
    """Fail-closed live Alpaca gate for new buys; stale/missing snapshots never qualify."""
    from alpaca.data.enums import DataFeed
    from alpaca.data.historical import StockHistoricalDataClient
    from alpaca.data.requests import StockSnapshotRequest
    key,secret=credentials();feed_name=os.getenv("ALPACA_DATA_FEED","iex").lower();feed=DataFeed.SIP if feed_name=="sip" else DataFeed.IEX
    market=StockHistoricalDataClient(key,secret);threshold=number(os.getenv("MOSQUITO_ENTRY_CONFIRMATION_PCT"))
    if threshold is None:threshold=0.005
    if threshold<0 or threshold>0.10:raise RuntimeError("entry confirmation threshold is outside the safe range")
    rows={str(row.get("ticker") or "").upper():dict(row) for row in picks};qualified=[];utc_now=datetime.now(timezone.utc)
    symbols=list(rows)
    for offset in range(0,len(symbols),60):
        batch=symbols[offset:offset+60]
        snapshots=market.get_stock_snapshot(StockSnapshotRequest(symbol_or_symbols=batch,feed=feed)) or {}
        for symbol in batch:
            snap=snapshots.get(symbol);bar=getattr(snap,"daily_bar",None);trade=getattr(snap,"latest_trade",None)
            opened=number(getattr(bar,"open",None));current=number(getattr(trade,"price",None));stamp=getattr(trade,"timestamp",None)
            if stamp is None:continue
            if stamp.tzinfo is None:stamp=stamp.replace(tzinfo=timezone.utc)
            if utc_now-stamp.astimezone(timezone.utc)>timedelta(minutes=2):continue
            if not entry_signal_met(opened,current,threshold):continue
            row=rows[symbol];row.update(price=current,session_open=opened,entry_signal_pct=(current/opened-1)*100,entry_confirmed=True);qualified.append(row)
    return sorted(qualified,key=lambda row:(row.get("rank",10**9),-float(row.get("score",0))))
def alpaca_history(symbols):
    import pandas as pd
    from alpaca.data.enums import DataFeed
    from alpaca.data.historical import StockHistoricalDataClient
    from alpaca.data.requests import StockBarsRequest
    from alpaca.data.timeframe import TimeFrame
    key,secret=credentials();feed_name=os.getenv("ALPACA_DATA_FEED","iex").lower();feed=DataFeed.SIP if feed_name=="sip" else DataFeed.IEX
    market=StockHistoricalDataClient(key,secret);closes=[];volumes=[];start=datetime.now(timezone.utc)-timedelta(days=5*366)
    for offset in range(0,len(symbols),60):
        batch=symbols[offset:offset+60];bars=market.get_stock_bars(StockBarsRequest(symbol_or_symbols=batch,start=start,timeframe=TimeFrame.Day,feed=feed));frame=bars.df
        if frame is None or frame.empty:continue
        if isinstance(frame.index,pd.MultiIndex):
            closes.append(frame["close"].unstack(level="symbol"));volumes.append(frame["volume"].unstack(level="symbol"))
    if not closes:raise RuntimeError("Alpaca market history is unavailable")
    close=pd.concat(closes,axis=1).sort_index();volume=pd.concat(volumes,axis=1).reindex(close.index)
    close.columns=[str(c).upper() for c in close.columns];volume.columns=[str(c).upper() for c in volume.columns]
    return close.loc[:,~close.columns.duplicated()],volume.loc[:,~volume.columns.duplicated()]
def orchestrate_open(*,session_date,idempotency_key,paper_only):
    if not paper_only or not paper():raise RuntimeError("paper-only launch required")
    state=load_state()
    if not state.get("armed",True):return {"session_date":session_date,"status":"disarmed","paper_only":True}
    if not enabled():return {"session_date":session_date,"status":"broker_execution_disabled","orders":0,"paper_only":True}
    broker=client();account=account_data();bp=number(account.get("buying_power")) or 0;requested=number(state.get("requested_investment")) or min(50000,bp);allocation=min(requested,bp)
    if allocation<=0:raise RuntimeError("No paper buying power is available")
    picks=tradable_picks(broker,100);confirmed=confirmed_entry_picks(picks);life=lifecycle_for(broker);current=life.reconcile()
    if not current.get("positions") and not current.get("orders"):
        if len(confirmed)<50:raise RuntimeError(f"Entry gate waiting: {len(confirmed)}/50 candidates passed the configured rise above session open")
        result=life.enter(confirmed,allocation)
    else:
        selected={p["ticker"] for p in picks[:50]};dead=set(current.get("positions",{}))-selected;prices={p["ticker"]:p["price"] for p in picks}
        result=life.rebalance(confirmed,prices,bp,dead_symbols=dead)
    state.update(running=True,armed=True,allocation=allocation,requested_investment=requested,last_scan=now(),last_error=None);save_state(state);ensure_engine()
    return {"session_date":session_date,"idempotency_key":idempotency_key,"selected":50,"eligible_candidates":len(picks),"orders":len(result.get("orders",{})),"allocation":allocation,"paper_only":True}
def broker_calendar(*,start,end):
    from alpaca.trading.requests import GetCalendarRequest
    return client().get_calendar(GetCalendarRequest(start=date.fromisoformat(start),end=date.fromisoformat(end)))
def scheduler_instance():return MarketScheduler(lambda:client().get_clock(),broker_calendar,orchestrate_open,state_path=os.getenv("MOSQUITO_SCHEDULER_FILE","/data/mosquito-scheduler.json"),paper_only=True)
def ensure_scheduler():
    global SCHEDULER_THREAD
    with SCHEDULER_THREAD_LOCK:
        if SCHEDULER_THREAD is None or not SCHEDULER_THREAD.is_alive():
            scheduler=scheduler_instance();SCHEDULER_THREAD=threading.Thread(target=scheduler.run_forever,args=(RUNTIME_STOP,),kwargs={"poll_seconds":15},name="mosquito-market-scheduler",daemon=True);SCHEDULER_THREAD.start()
def reset_paths():
    return [STATE_PATH,engine.PATH,Path(os.getenv("MOSQUITO_LIFECYCLE_FILE","/data/mosquito-lifecycle.json")),Path(os.getenv("MOSQUITO_SCHEDULER_FILE","/data/mosquito-scheduler.json")),Path(os.getenv("MOSQUITO_SIM_FILE","/data/mosquito-simulation.json"))]
def reset_marker():return Path(os.getenv("MOSQUITO_RESET_MARKER","/data/mosquito-reset-marker.json"))
def reset_status():
    try:return json.loads(Path(os.getenv("MOSQUITO_RESET_STATUS","/data/mosquito-reset-status.json")).read_text())
    except (OSError,ValueError):return {"status":"not_requested"}
def write_reset_status(value):
    path=Path(os.getenv("MOSQUITO_RESET_STATUS","/data/mosquito-reset-status.json"));path.parent.mkdir(parents=True,exist_ok=True);path.write_text(json.dumps(value,separators=(",",":")))
def reset_done(reset_id):
    try:return json.loads(reset_marker().read_text()).get("reset_id")==reset_id
    except (OSError,ValueError):return False
def run_paper_reset(reset_id):
    try:
        if not paper():raise RuntimeError("paper-only reset required")
        write_reset_status({"status":"closing_old_paper_session","reset_id":reset_id,"started_at":now()})
        broker=client();broker.close_all_positions(cancel_orders=True)
        deadline=time.monotonic()+120
        while time.monotonic()<deadline:
            if not broker.get_all_positions():break
            time.sleep(2)
        remaining=broker.get_all_positions()
        if remaining:raise RuntimeError(f"paper positions still open: {len(remaining)}")
        for path in reset_paths():
            try:path.unlink()
            except FileNotFoundError:pass
        account=broker.get_account();baseline=number(getattr(account,"equity",None))
        state={**DEFAULTS,"allocation":50000.0,"requested_investment":50000.0,"baseline_equity":baseline,"running":True,"armed":True,"last_error":None};save_state(state)
        marker=reset_marker();marker.parent.mkdir(parents=True,exist_ok=True);marker.write_text(json.dumps({"reset_id":reset_id,"completed_at":now()},separators=(",",":")))
        write_reset_status({"status":"complete","reset_id":reset_id,"allocation":50000.0,"old_positions_remaining":0,"completed_at":now()})
        with LOCK:CACHE.clear()
        ensure_engine();ensure_scheduler()
    except Exception as exc:
        write_reset_status({"status":"failed","reset_id":reset_id,"error_type":type(exc).__name__,"failed_at":now()})
        app.logger.error("paper reset failed: %s",type(exc).__name__)
def ensure_reset():
    global RESET_THREAD
    reset_id=str(os.getenv("MOSQUITO_RESET_ID","")).strip()
    if not reset_id or reset_done(reset_id):return True
    with RESET_THREAD_LOCK:
        if RESET_THREAD is None or not RESET_THREAD.is_alive():
            RESET_THREAD=threading.Thread(target=run_paper_reset,args=(reset_id,),name="mosquito-paper-reset",daemon=True);RESET_THREAD.start()
    return False
def ensure_runtime():
    if truthy("MOSQUITO_RUNTIME_ENABLED") or bool(os.getenv("RAILWAY_ENVIRONMENT")):
        if ensure_reset():ensure_engine();ensure_scheduler()
def serial(v):
    if v is None or isinstance(v,(str,bool,int,float)): return v
    if isinstance(v,datetime): return v.isoformat()
    if isinstance(v,(list,tuple)): return [serial(x) for x in v]
    if isinstance(v,dict): return {str(k):serial(x) for k,x in v.items()}
    if hasattr(v,"model_dump"): return serial(v.model_dump(mode="json"))
    if hasattr(v,"dict"): return serial(v.dict())
    return str(v)
def number(v):
    try: n=float(v); return n if math.isfinite(n) else None
    except (TypeError,ValueError): return None
def load_state():
    with LOCK:
        try:
            raw=json.loads(STATE_PATH.read_text())
            if not isinstance(raw,dict): raw={}
        except (OSError,ValueError): raw={}
        state={**DEFAULTS,**{k:raw[k] for k in DEFAULTS if k in raw}}
        # States written before requested_investment existed used allocation for
        # both the owner's request and the buying-power-capped allocation.
        if "requested_investment" not in raw:state["requested_investment"]=state["allocation"]
        return state
def save_state(state):
    state={**DEFAULTS,**{k:state[k] for k in DEFAULTS if k in state},"updated_at":now()}
    with LOCK:
        STATE_PATH.parent.mkdir(parents=True,exist_ok=True)
        fd,name=tempfile.mkstemp(prefix=".mosquito-",dir=str(STATE_PATH.parent))
        try:
            with os.fdopen(fd,"w") as f: json.dump(state,f,separators=(",",":"))
            os.replace(name,STATE_PATH)
        finally:
            try: os.unlink(name)
            except FileNotFoundError: pass
def cached(key,ttl,loader):
    t=time.monotonic()
    with LOCK:
        hit=CACHE.get(key)
        if hit and hit[0]>t:return hit[1]
    value=loader()
    with LOCK:CACHE[key]=(t+ttl,value)
    return value
def error(message,status=400): return jsonify(error=message,timestamp=now()),status
def upstream(exc,operation="broker_request",endpoint=None):log_broker_error(exc,operation,endpoint);return error("Broker service is temporarily unavailable",503)
def authorized():
    expected=os.getenv("DASHBOARD_TOKEN")
    if not expected:return True
    auth=request.headers.get("Authorization",""); supplied=auth[7:].strip() if auth.lower().startswith("bearer ") else None
    supplied=supplied or request.headers.get("X-Dashboard-Token") or request.args.get("token") or request.cookies.get("mosquito_access")
    return bool(supplied) and hmac.compare_digest(supplied,expected)
@app.before_request
def protect():
    ensure_runtime()
    if request.path.startswith("/api/") and not authorized():return error("Unauthorized",401)
@app.after_request
def headers(response):
    for k,v in {"X-Content-Type-Options":"nosniff","X-Frame-Options":"DENY","Referrer-Policy":"no-referrer","Permissions-Policy":"camera=(), microphone=(), geolocation=()","Content-Security-Policy":"default-src 'self'; img-src 'self' data:; style-src 'self' 'unsafe-inline'; script-src 'self' 'unsafe-inline'; connect-src 'self'"}.items():response.headers[k]=v
    if request.path.startswith("/api/"):response.headers["Cache-Control"]="no-store"
    token=request.args.get("token");expected=os.getenv("DASHBOARD_TOKEN")
    if token and expected and hmac.compare_digest(token,expected):response.set_cookie("mosquito_access",token,httponly=True,secure=request.is_secure,samesite="Strict",max_age=2592000)
    return response
def rate_limit(limit=20,window=60):
    def deco(fn):
        @wraps(fn)
        def wrap(*a,**kw):
            ident=f"{request.remote_addr}:{request.path}";t=time.monotonic()
            with LOCK:
                q=CALLS[ident]
                while q and q[0]<=t-window:q.popleft()
                if len(q)>=limit:return error("Too many requests; try again shortly",429)
                q.append(t)
            return fn(*a,**kw)
        return wrap
    return deco
@app.get("/")
def root():
    if os.getenv("DASHBOARD_TOKEN") and not authorized(): return redirect("/login")
    return make_response(send_from_directory(Path(app.root_path)/"templates","index.html"))
@app.route("/login",methods=["GET","POST"])
def login():
    if not os.getenv("DASHBOARD_TOKEN"): return redirect("/")
    if request.method=="POST":
        token=request.form.get("token","")
        if hmac.compare_digest(token,os.getenv("DASHBOARD_TOKEN","")):
            response=make_response(redirect("/"));response.set_cookie("mosquito_access",token,httponly=True,secure=request.is_secure,samesite="Strict",max_age=2592000);return response
    return """<!doctype html><meta name=viewport content='width=device-width'><title>Mosquito Login</title><style>body{background:#050705;color:#fff;font:18px system-ui;display:grid;place-items:center;height:100vh;margin:0}form{width:min(85vw,380px);padding:30px;border:1px solid #35452a;border-radius:18px;background:#0c100b}input,button{box-sizing:border-box;width:100%;padding:15px;margin-top:15px;border-radius:10px}button{background:#76ed0b;font-weight:800}</style><form method=post><h1>MOSQUITO</h1><label>Owner access token<input name=token type=password required autofocus></label><button>OPEN DASHBOARD</button></form>""",(401 if request.method=="POST" else 200)
@app.get("/<path:name>")
def assets(name):
    if not name.startswith("static/"):return error("Not found",404)
    asset=name.removeprefix("static/")
    if asset not in {"style.css","app.js","mosquito-hero.webp","mosquito-hero-profit.webp"}:return error("Not found",404)
    return send_from_directory(Path(app.root_path)/"static",asset)
@app.get("/health")
def health():return jsonify(status="ok",service="mosquito-runner",timestamp=now())
@app.get("/ready")
def ready():
    k,s=credentials();return jsonify(status="ready",alpaca_configured=bool(k and s),state_writable=os.access(STATE_PATH.parent,os.W_OK),timestamp=now())
@app.get("/broker-health")
def broker_health():
    result=broker_health_data();return jsonify(result),(200 if result["status"]=="ok" else 503)
@app.get("/reset-health")
def reset_health():return jsonify(reset_status())
@app.get("/session-health")
def session_health():
    """Public, non-sensitive launch proof: counts and states only."""
    scheduler_path=Path(os.getenv("MOSQUITO_SCHEDULER_FILE","/data/mosquito-scheduler.json"))
    try:scheduler=json.loads(scheduler_path.read_text())
    except (OSError,ValueError):scheduler={}
    result=scheduler.get("result") if isinstance(scheduler.get("result"),dict) else {}
    payload={"status":"ok","paper_mode":paper(),"allocation":load_state().get("requested_investment"),"running":load_state().get("running"),"entry_confirmation_pct":number(os.getenv("MOSQUITO_ENTRY_CONFIRMATION_PCT")) or 0.005,"trailing_drop_pct":0.10,"scheduler_status":scheduler.get("status","waiting"),"scheduler_error_type":scheduler.get("error_type"),"selected":result.get("selected"),"eligible_candidates":result.get("eligible_candidates"),"submitted_orders":result.get("orders"),"timestamp":now()}
    try:
        payload["open_positions"]=len(client().get_all_positions())
        from alpaca.trading.enums import QueryOrderStatus
        from alpaca.trading.requests import GetOrdersRequest
        payload["open_orders"]=len(client().get_orders(filter=GetOrdersRequest(status=QueryOrderStatus.OPEN,limit=100)))
    except Exception:payload.update(status="degraded",open_positions=None,open_orders=None)
    return jsonify(payload),(200 if payload["status"]=="ok" else 503)
def account_data():
    a=client().get_account(); fields=("id","status","currency","cash","portfolio_value","equity","last_equity","buying_power","daytrading_buying_power","regt_buying_power","trading_blocked","transfers_blocked","account_blocked","pattern_day_trader","daytrade_count")
    result={**{f:serial(getattr(a,f,None)) for f in fields},"connected":True,"mode":"paper" if paper() else "live"}
    equity,last=number(result.get("equity")),number(result.get("last_equity"));raw_equity=equity
    result["day_profit"]=(equity-last) if equity is not None and last is not None else None
    result["day_profit_pct"]=((equity-last)/last*100) if equity is not None and last not in (None,0) else None
    saved=load_state();starting=number(saved.get("requested_investment"));baseline=number(saved.get("baseline_equity"))
    if raw_equity is not None and baseline is not None and starting not in (None,0):
        strategy_profit=raw_equity-baseline;result["broker_equity"]=result.get("equity");result["broker_portfolio_value"]=result.get("portfolio_value");result["equity"]=starting+strategy_profit;result["portfolio_value"]=starting+strategy_profit;result["day_profit"]=strategy_profit;result["day_profit_pct"]=strategy_profit/starting*100
        equity=starting+strategy_profit
    result["total_profit"]=(equity-starting) if equity is not None and starting not in (None,0) else None
    result["total_profit_pct"]=((equity-starting)/starting*100) if equity is not None and starting not in (None,0) else None
    return result
@app.get("/api/account")
def account():
    try:return jsonify(cached("account",10,account_data))
    except Exception as exc:return upstream(exc,"get_account","/v2/account")
def position_rows():
    fields=("asset_id","symbol","exchange","asset_class","qty","side","market_value","cost_basis","unrealized_pl","unrealized_plpc","current_price","lastday_price","change_today","avg_entry_price")
    rows=[];tracked={p["symbol"]:p for p in engine.public_state()["positions"]}
    for p in client().get_all_positions():
        row={f:serial(getattr(p,f,None)) for f in fields}
        qty=number(row.get("qty")); entry=number(row.get("avg_entry_price")); current=number(row.get("current_price")); cost=number(row.get("cost_basis"))
        if entry is None and qty not in (None,0) and cost is not None:entry=cost/qty
        if entry and current:
            guard=protected_state(entry,current,tracked.get(str(row.get("symbol")),{}).get("peak_price"))
            row.update({k:round(v,6) if isinstance(v,float) else v for k,v in guard.items()})
        rows.append(row)
    return rows
@app.get("/api/engine")
def engine_status():return jsonify(engine.public_state())
@app.get("/api/positions")
def positions():
    try:r=cached("positions",8,position_rows);return jsonify(positions=r,count=len(r),timestamp=now())
    except Exception as exc:return upstream(exc,"get_positions","/v2/positions")
@app.get("/api/orders")
def orders():
    try:
        status=request.args.get("status","all").lower()
        if status not in {"all","open","closed"}:return error("status must be all, open, or closed")
        def get():
            from alpaca.trading.enums import QueryOrderStatus
            from alpaca.trading.requests import GetOrdersRequest
            return [serial(o) for o in client().get_orders(filter=GetOrdersRequest(status={"all":QueryOrderStatus.ALL,"open":QueryOrderStatus.OPEN,"closed":QueryOrderStatus.CLOSED}[status],limit=100))]
        r=cached("orders:"+status,8,get);return jsonify(orders=r,count=len(r),timestamp=now())
    except Exception as exc:return upstream(exc,"get_orders","/v2/orders")
@app.get("/api/portfolio-history")
def history():
    period=request.args.get("period","1D");frame=request.args.get("timeframe","5Min")
    if period not in {"1D","1W","1M","3M","1A"} or frame not in {"1Min","5Min","15Min","1H","1D"}:return error("Unsupported history period or timeframe")
    try:
        def get():
            from alpaca.trading.requests import GetPortfolioHistoryRequest
            h=client().get_portfolio_history(GetPortfolioHistoryRequest(period=period,timeframe=frame,extended_hours=True))
            return {"timestamp":serial(h.timestamp),"equity":serial(h.equity),"profit_loss":serial(h.profit_loss),"profit_loss_pct":serial(h.profit_loss_pct),"base_value":serial(h.base_value),"period":period,"timeframe":frame}
        return jsonify(cached(f"history:{period}:{frame}",30,get))
    except Exception as exc:return upstream(exc,"get_portfolio_history","/v2/account/portfolio/history")
@app.get("/api/config")
def get_config():
    s=load_state();return jsonify(**{k:s[k] for k in ("allocation","daily_goal","updated_at")})
def nonnegative(v,label):
    n=number(v)
    if n is None or n<0:raise ValueError(f"{label} must be a finite, nonnegative number")
    return n
@app.post("/api/config")
@rate_limit()
def set_config():
    body=request.get_json(silent=True)
    if not isinstance(body,dict):return error("A JSON object is required")
    s=load_state()
    try:
        if "allocation" in body:s["allocation"]=nonnegative(body["allocation"],"allocation")
        if "daily_goal" in body:s["daily_goal"]=nonnegative(body["daily_goal"],"daily_goal")
    except ValueError as exc:return error(str(exc))
    save_state(s);s=load_state();return jsonify(**{k:s[k] for k in ("allocation","daily_goal","updated_at")})
@app.post("/api/scan")
@rate_limit(6)
def scan():
    try:
        count=int((request.get_json(silent=True) or {}).get("count",50))
        if not 1<=count<=200:raise ValueError
        r=cached(f"scan:{count}",300,lambda:build_watchlist(count));s=load_state();s.update(last_scan=r.get("generated_at",now()),last_error=None);save_state(s);return jsonify(r)
    except (TypeError,ValueError):return error("count must be an integer from 1 through 200")
    except Exception as exc:
        s=load_state();s["last_error"]="Market scan unavailable";save_state(s);app.logger.warning("scan failure: %s",type(exc).__name__);return error("Market scan is temporarily unavailable",503)
def status_data():
    k,s=credentials();st=load_state();return {"name":"Mosquito AI Trading Bot","strategy":"V5.8 Master","running":bool(st["running"]),"mode":"paper" if paper() else "live","alpaca_configured":bool(k and s),"order_execution":enabled(),"order_execution_enabled":enabled(),"allocation":st["allocation"],"investment_amount":st["requested_investment"],"daily_goal":st["daily_goal"],"exit_status":st["exit_status"],"exit_request_id":st["exit_request_id"],"risk_status":"SAFE" if paper() else "LIVE","risk_detail":"Paper trading mode" if paper() else "Live execution enabled","last_scan":st["last_scan"],"last_error":st["last_error"],"timestamp":now()}
@app.get("/api/status")
def status():return jsonify(status_data())
@app.get("/api/alerts")
def alerts():
    out=[];s=load_state()
    if s["last_error"]:out.append({"level":"error","code":"LAST_ERROR","message":s["last_error"]})
    try:
        a=cached("account",10,account_data)
        for field,message in (("account_blocked","Alpaca account is blocked"),("trading_blocked","Trading is blocked"),("transfers_blocked","Transfers are blocked")):
            if a.get(field) is True:out.append({"level":"critical","code":field.upper(),"message":message})
    except Exception:out.append({"level":"warning","code":"BROKER_UNAVAILABLE","message":"Broker status is unavailable"})
    return jsonify(alerts=out,count=len(out),timestamp=now())
@app.get("/api/dashboard")
def dashboard_data():
    st=load_state();r={"status":status_data(),"config":{"allocation":st["allocation"],"daily_goal":st["daily_goal"]},"timestamp":now(),"errors":[]}
    try:r["account"]=cached("account",10,account_data)
    except Exception:r["account"]=None;r["errors"].append("account")
    try:r["positions"]=cached("positions",8,position_rows)
    except Exception:r["positions"]=None;r["errors"].append("positions")
    r["positions_count"]=len(r["positions"]) if isinstance(r["positions"],list) else None
    r.update(trades_today=None,win_rate=None,trades=[],performance=[])
    try:
        from alpaca.trading.enums import QueryOrderStatus
        from alpaca.trading.requests import GetOrdersRequest
        orders=[serial(o) for o in client().get_orders(filter=GetOrdersRequest(status=QueryOrderStatus.CLOSED,limit=100))]
        today=datetime.now(timezone.utc).date();filled=[];all_filled=[]
        for order in orders:
            stamp=order.get("filled_at")
            try:is_today=datetime.fromisoformat(str(stamp).replace("Z","+00:00")).astimezone(timezone.utc).date()==today
            except (TypeError,ValueError):is_today=False
            if order.get("filled_qty") not in (None,"0",0):
                all_filled.append(order)
                if is_today:filled.append(order)
        r["trades"]=[{"symbol":o.get("symbol"),"side":o.get("side"),"qty":o.get("filled_qty"),"price":o.get("filled_avg_price"),"timestamp":o.get("filled_at"),"status":o.get("status")} for o in all_filled]
        r["trades_today"]=len(filled)
        try:
            retired=lifecycle_for(client())._load().get("retired",{});wins=0
            for symbol,lot in retired.items():
                if not lot.get("exit_price"):continue
                r["trades"].extend([{"symbol":symbol,"side":"buy","qty":lot.get("qty"),"price":lot.get("entry_price"),"timestamp":lot.get("entry_at"),"status":"filled"},{"symbol":symbol,"side":"sell","qty":lot.get("qty"),"price":lot.get("exit_price"),"timestamp":lot.get("exited_at"),"status":"filled"}])
                if number(lot.get("exit_price"))>=number(lot.get("entry_price")):wins+=1
            r["win_rate"]=(wins/len(retired)*100) if retired else None
        except Exception:r["errors"].append("lifecycle_history")
    except Exception:r["errors"].append("orders")
    try:
        from alpaca.trading.requests import GetPortfolioHistoryRequest
        h=client().get_portfolio_history(GetPortfolioHistoryRequest(period="1D",timeframe="5Min",extended_hours=True))
        stamps,values=serial(h.timestamp),serial(h.equity)
        r["history"]=[{"timestamp":t,"equity":v} for t,v in zip(stamps,values)]
        if values:
            first,last=number(values[0]),number(values[-1])
            r["performance"]=[{"label":"Today","profit":(last-first) if None not in (first,last) else None,"return_pct":((last-first)/first*100) if first not in (None,0) and last is not None else None,"trades":r["trades_today"]}]
    except Exception:r["history"]=[];r["errors"].append("history")
    alerts_=[]
    if st["last_error"]:alerts_.append({"type":"error","message":st["last_error"]})
    if truthy("MOSQUITO_ENABLE_SIMULATION"):
        try:r["green_ribbon"]=cached("green_ribbon",60,simulator.value)
        except Exception:r["green_ribbon"]={"status":"UNAVAILABLE","error":"Paper simulation prices are temporarily unavailable"};r["errors"].append("green_ribbon")
    else:r["green_ribbon"]={"status":"DISABLED"}
    r["alerts"]=alerts_;r["alerts_count"]=len(alerts_)
    return jsonify(r)
@app.post("/api/bot/start")
@rate_limit()
def start():
    s=load_state();body=request.get_json(silent=True) or {}
    try:req=nonnegative(body.get("allocation",body.get("investment_amount",s["requested_investment"])),"allocation")
    except ValueError as exc:return error(str(exc))
    if req<=0:return error("allocation must be greater than zero")
    s["requested_investment"]=req
    try:save_state(s)
    except OSError as exc:
        app.logger.error("state persistence failure: %s",type(exc).__name__)
        return error("Bot state could not be saved",500)
    try:
        bp=number(cached("account",1,account_data).get("buying_power"))
        if bp is None:
            s["last_error"]="Buying power is unavailable";save_state(s)
            return error("Buying power is unavailable",409)
        allocation=min(req,max(0,bp))
        if allocation<=0:
            s["last_error"]="No buying power is available";save_state(s)
            return error("No buying power is available",409)
    except Exception as exc:
        s["last_error"]="Broker service is temporarily unavailable"
        try:save_state(s)
        except OSError as state_exc:app.logger.error("state persistence failure after broker error: %s",type(state_exc).__name__)
        return upstream(exc,"start_bot_get_account","/v2/account")
    already=bool(s["running"]);s.update(running=True,armed=True,allocation=allocation,last_error=None)
    simulation=None
    if truthy("MOSQUITO_ENABLE_SIMULATION"):
        try:simulation=simulator.begin(allocation)
        except Exception as exc:
            s.update(running=False,last_error="V5.8 paper simulation could not start");save_state(s);app.logger.warning("simulation start failure: %s",type(exc).__name__);return error("V5.8 paper simulation could not start",503)
    try:save_state(s)
    except OSError as exc:
        app.logger.error("state persistence failure: %s",type(exc).__name__)
        return error("Bot state could not be saved",500)
    with LOCK:CACHE.clear()
    ensure_runtime()
    return jsonify(ok=True,running=True,already_running=already,allocation=allocation,requested_allocation=req,capped=allocation<req,execution_enabled=enabled(),simulation_enabled=bool(simulation),mode="paper" if paper() else "live",timestamp=now())
@app.post("/api/bot/stop")
@rate_limit()
def stop():
    s=load_state();already=not bool(s["running"]);s.update(running=False,armed=False)
    if truthy("MOSQUITO_ENABLE_SIMULATION"):simulator.stop()
    try:save_state(s)
    except OSError as exc:
        app.logger.error("state persistence failure: %s",type(exc).__name__)
        return error("Bot state could not be saved",500)
    return jsonify(ok=True,running=False,already_stopped=already,positions_unchanged=True,
        message="New entries are stopped; existing positions were not changed",timestamp=now())
@app.post("/api/bot/exit")
@rate_limit(5)
def exit_bot():
    body=request.get_json(silent=True)
    if not isinstance(body,dict) or body.get("confirm")!="EXIT ALL POSITIONS":
        return error('confirmation is required; send {"confirm":"EXIT ALL POSITIONS"}',400)
    if truthy("MOSQUITO_ENABLE_SIMULATION") and not body.get("broker_exit"):
        try:
            report=simulator.exit_all();completed=bool(report.get("completed",True));status="completed" if completed else "partially_blocked"
            message="Mosquito simulated positions were closed" if completed else "Profitable positions were closed; positions below purchase price remain held"
            s=load_state();s.update(running=not completed,exit_status=status,last_error=None);save_state(s);CACHE.clear()
            return jsonify(ok=completed,running=not completed,executed=True,submitted=False,completed=completed,status=status,message=message,green_ribbon=report,timestamp=now()),(200 if completed else 409)
        except Exception as exc:app.logger.warning("simulation exit failure: %s",type(exc).__name__);return error("Simulated positions could not be closed",503)
    request_id=str(body.get("request_id","")).strip()
    if len(request_id)>128:return error("request_id must be 128 characters or fewer")
    if request_id:
        with LOCK:
            prior=EXIT_RESULTS.get(request_id)
        if prior:
            http_status=prior["http_status"]
            return jsonify({k:v for k,v in prior.items() if k!="http_status"}),http_status
    if not EXIT_LOCK.acquire(blocking=False):
        return error("An exit request is already in progress",409)
    try:
        s=load_state();s.update(running=False,exit_status="requested",exit_request_id=request_id or None,last_error=None)
        try:save_state(s)
        except OSError as exc:
            app.logger.error("state persistence failure: %s",type(exc).__name__)
            return error("Bot state could not be saved; no broker request was sent",500)
        if not enabled():
            s.update(exit_status="blocked",last_error="Exit was not submitted because broker execution is disabled")
            try:save_state(s)
            except OSError as exc:app.logger.error("state persistence failure: %s",type(exc).__name__)
            return jsonify(ok=False,running=False,executed=False,submitted=False,completed=False,
                status="blocked",message="Exit was not submitted because broker execution is disabled",timestamp=now()),409
        broker=client(); positions=broker.get_all_positions(); rows=[];failures=[];blocked=[]
        from alpaca.trading.enums import OrderSide,TimeInForce
        from alpaca.trading.requests import LimitOrderRequest
        for p in positions:
            symbol=str(getattr(p,"symbol",""));qty=number(getattr(p,"qty",None));entry=number(getattr(p,"avg_entry_price",None));current=number(getattr(p,"current_price",None))
            if not symbol or qty is None or qty<=0 or entry is None or current is None:
                failures.append({"symbol":symbol or "unknown","reason":"position data unavailable"});continue
            guard=protected_state(entry,current)
            if guard["below_entry"]:
                blocked.append({"symbol":symbol,"qty":qty,**guard});continue
            try:
                order=broker.submit_order(order_data=LimitOrderRequest(symbol=symbol,qty=qty,side=OrderSide.SELL,time_in_force=TimeInForce.DAY,limit_price=guard["protected_floor"],client_order_id=f"mosquito-exit-{symbol}-{int(time.time())}"))
                rows.append({"symbol":symbol,"qty":qty,"entry_price":entry,"current_price":current,"limit_price":guard["protected_floor"],"status":serial(getattr(order,"status","accepted")),"order_id":serial(getattr(order,"id",None)),"reason":"OWNER_PROTECTED_EXIT"})
            except Exception:
                failures.append({"symbol":symbol,"reason":"broker rejected protected limit order"})
        status="partial_failure" if failures else "partially_blocked" if blocked else "pending"
        message=("Some protected limit orders were rejected; verify the remaining positions" if failures else
            "Profitable positions received protected limit orders; below-purchase positions remain held" if blocked else
            "Protected limit orders were accepted by Alpaca and are pending; completion has not been verified")
        s.update(exit_status=status,last_error=message if failures else None)
        try:save_state(s)
        except OSError as exc:app.logger.error("state persistence failure: %s",type(exc).__name__)
        payload={"ok":not failures,"running":False,"executed":True,"submitted":True,"completed":False,
            "status":status,"message":message,"results":rows,"failures":failures,"blocked_below_purchase":blocked,
            "mode":"paper" if paper() else "live","request_id":request_id or None,"timestamp":now()}
        http_status=502 if failures else 409 if blocked else 202
        if request_id:
            with LOCK:EXIT_RESULTS[request_id]={**payload,"http_status":http_status}
        with LOCK:CACHE.clear()
        return jsonify(payload),http_status
    except Exception as exc:
        s=load_state();s.update(running=False,exit_status="failed",last_error="Broker rejected or could not process the exit request")
        try:save_state(s)
        except OSError as state_exc:app.logger.error("state persistence failure after broker error: %s",type(state_exc).__name__)
        with LOCK:CACHE.clear()
        return upstream(exc,"exit_bot")
    finally:EXIT_LOCK.release()
if __name__=="__main__":app.run(host="0.0.0.0",port=int(os.getenv("PORT","8080")))
