"""Production API for Mosquito Runner; paper-safe by default."""
from __future__ import annotations
import hmac,json,math,os,tempfile,threading,time
from collections import defaultdict,deque
from datetime import datetime,timezone
from functools import wraps
from pathlib import Path
from flask import Flask,jsonify,make_response,request,send_from_directory,redirect
from strategy import build_watchlist

app=Flask(__name__,static_folder=None); app.config["MAX_CONTENT_LENGTH"]=65536
LOCK=threading.RLock(); EXIT_LOCK=threading.Lock(); CACHE={}; CALLS=defaultdict(deque); EXIT_RESULTS={}
STATE_PATH=Path(os.getenv("MOSQUITO_STATE_FILE","/tmp/mosquito-runner-state.json"))
DEFAULTS={"allocation":0.0,"requested_investment":0.0,"daily_goal":500.0,"running":False,"exit_status":None,"exit_request_id":None,"last_scan":None,"last_error":None,"updated_at":None}
def now(): return datetime.now(timezone.utc).isoformat()
def truthy(name): return os.getenv(name,"").lower() in {"1","true","yes","on"}
def paper(): return not(truthy("ALPACA_LIVE_TRADING") and truthy("ALPACA_ENABLE_ORDER_EXECUTION"))
def enabled(): return truthy("ALPACA_ENABLE_ORDER_EXECUTION")
def credentials():
    return (os.getenv("ALPACA_API_KEY") or os.getenv("ALPACA_API_KEY_ID") or os.getenv("APCA_API_KEY_ID"),os.getenv("ALPACA_SECRET_KEY") or os.getenv("ALPACA_API_SECRET_KEY") or os.getenv("APCA_API_SECRET_KEY"))
def client():
    key,secret=credentials()
    if not key or not secret: raise RuntimeError("missing credentials")
    from alpaca.trading.client import TradingClient
    return TradingClient(key,secret,paper=paper())
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
def upstream(exc): app.logger.warning("upstream failure: %s",type(exc).__name__);return error("Broker service is temporarily unavailable",503)
def authorized():
    expected=os.getenv("DASHBOARD_TOKEN")
    if not expected:return True
    auth=request.headers.get("Authorization",""); supplied=auth[7:].strip() if auth.lower().startswith("bearer ") else None
    supplied=supplied or request.headers.get("X-Dashboard-Token") or request.args.get("token") or request.cookies.get("mosquito_access")
    return bool(supplied) and hmac.compare_digest(supplied,expected)
@app.before_request
def protect():
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
    if asset not in {"style.css","app.js","mosquito.svg"}:return error("Not found",404)
    return send_from_directory(Path(app.root_path)/"static",asset)
@app.get("/health")
def health():return jsonify(status="ok",service="mosquito-runner",timestamp=now())
@app.get("/ready")
def ready():
    k,s=credentials();return jsonify(status="ready",alpaca_configured=bool(k and s),state_writable=os.access(STATE_PATH.parent,os.W_OK),timestamp=now())
def account_data():
    a=client().get_account(); fields=("id","status","currency","cash","portfolio_value","equity","last_equity","buying_power","daytrading_buying_power","regt_buying_power","trading_blocked","transfers_blocked","account_blocked","pattern_day_trader","daytrade_count")
    result={**{f:serial(getattr(a,f,None)) for f in fields},"connected":True,"mode":"paper" if paper() else "live"}
    equity,last=number(result.get("equity")),number(result.get("last_equity"))
    result["day_profit"]=(equity-last) if equity is not None and last is not None else None
    result["day_profit_pct"]=((equity-last)/last*100) if equity is not None and last not in (None,0) else None
    result["total_profit"]=None;result["total_profit_pct"]=None
    return result
@app.get("/api/account")
def account():
    try:return jsonify(cached("account",10,account_data))
    except Exception as exc:return upstream(exc)
def position_rows():
    fields=("asset_id","symbol","exchange","asset_class","qty","side","market_value","cost_basis","unrealized_pl","unrealized_plpc","current_price","lastday_price","change_today")
    return [{f:serial(getattr(p,f,None)) for f in fields} for p in client().get_all_positions()]
@app.get("/api/positions")
def positions():
    try:r=cached("positions",8,position_rows);return jsonify(positions=r,count=len(r),timestamp=now())
    except Exception as exc:return upstream(exc)
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
    except Exception as exc:return upstream(exc)
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
    except Exception as exc:return upstream(exc)
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
        from alpaca.trading.requests import GetPortfolioHistoryRequest
        h=client().get_portfolio_history(GetPortfolioHistoryRequest(period="1D",timeframe="5Min",extended_hours=True))
        stamps,values=serial(h.timestamp),serial(h.equity)
        r["history"]=[{"timestamp":t,"equity":v} for t,v in zip(stamps,values)]
    except Exception:r["history"]=[];r["errors"].append("history")
    alerts_=[]
    if st["last_error"]:alerts_.append({"type":"error","message":st["last_error"]})
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
        return upstream(exc)
    already=bool(s["running"]);s.update(running=True,allocation=allocation,last_error=None)
    try:save_state(s)
    except OSError as exc:
        app.logger.error("state persistence failure: %s",type(exc).__name__)
        return error("Bot state could not be saved",500)
    return jsonify(ok=True,running=True,already_running=already,allocation=allocation,requested_allocation=req,capped=allocation<req,execution_enabled=enabled(),mode="paper" if paper() else "live",timestamp=now())
@app.post("/api/bot/stop")
@rate_limit()
def stop():
    s=load_state();already=not bool(s["running"]);s["running"]=False
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
        result=client().close_all_positions(cancel_orders=True)
        rows=serial(result); rows=rows if isinstance(rows,list) else [rows]
        failures=[row for row in rows if isinstance(row,dict) and isinstance(row.get("status"),int) and row["status"]>=400]
        status="partial_failure" if failures else "pending"
        message=("Some close requests were rejected; verify the remaining positions" if failures else
            "Close requests were accepted by Alpaca and are pending; completion has not been verified")
        s.update(exit_status=status,last_error=message if failures else None)
        try:save_state(s)
        except OSError as exc:app.logger.error("state persistence failure: %s",type(exc).__name__)
        payload={"ok":not failures,"running":False,"executed":True,"submitted":True,"completed":False,
            "status":status,"message":message,"results":rows,"failures":failures,
            "mode":"paper" if paper() else "live","request_id":request_id or None,"timestamp":now()}
        http_status=502 if failures else 202
        if request_id:
            with LOCK:EXIT_RESULTS[request_id]={**payload,"http_status":http_status}
        with LOCK:CACHE.clear()
        return jsonify(payload),http_status
    except Exception as exc:
        s=load_state();s.update(running=False,exit_status="failed",last_error="Broker rejected or could not process the exit request")
        try:save_state(s)
        except OSError as state_exc:app.logger.error("state persistence failure after broker error: %s",type(state_exc).__name__)
        with LOCK:CACHE.clear()
        return upstream(exc)
    finally:EXIT_LOCK.release()
if __name__=="__main__":app.run(host="0.0.0.0",port=int(os.getenv("PORT","8080")))
