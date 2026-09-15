import os
from datetime import datetime, timezone

from flask import Flask, jsonify, render_template, request

from strategy import build_watchlist

app = Flask(__name__)


@app.get("/")
def dashboard():
    return render_template("index.html")


@app.get("/health")
def health():
    return jsonify(status="ok", strategy="V5.8 Master")


@app.post("/api/scan")
def scan():
    body = request.get_json(silent=True) or {}
    count = max(25, min(int(body.get("count", 50)), 50))
    try:
        result = build_watchlist(count=count)
        return jsonify(result)
    except Exception as exc:
        return jsonify(error=str(exc)), 503


@app.get("/api/account")
def account():
    key = os.getenv("ALPACA_API_KEY")
    secret = os.getenv("ALPACA_SECRET_KEY")
    if not key or not secret:
        return jsonify(connected=False, mode="paper", message="Add Alpaca paper keys in Railway Variables")
    try:
        from alpaca.trading.client import TradingClient
        client = TradingClient(key, secret, paper=True)
        acct = client.get_account()
        return jsonify(connected=True, mode="paper", equity=float(acct.equity), buying_power=float(acct.buying_power))
    except Exception as exc:
        return jsonify(connected=False, mode="paper", message=str(exc)), 503


@app.get("/api/status")
def status():
    return jsonify(name="Mosquito AI Trading Bot", strategy="V5.8 Master", mode="paper-preview",
                   generated_at=datetime.now(timezone.utc).isoformat())


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", "8080")))
