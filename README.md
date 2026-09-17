# Mosquito V5.8 Master

Railway-compatible reconstruction of the locked V5.8 Master dashboard and
paper-account trailing monitor.

Mosquito is hard-locked to Alpaca paper mode in the application code. There is
no supported live-trading mode in this build.

The pure `build_shadow_watchlist` function in `reversal_strategy.py` is an
isolated, non-executable research screen. It accepts caller-supplied daily
open/close bars, ignores observations on or after `trade_date`, returns at most
25 audited picks, and has no connection to the broker lifecycle or V5.8 order
path.

V5.8's ranked output exposes `base_score`, `reversal_match`, the four
`reversal_metrics`, `reversal_overlay_score`, and
`reversal_score_contribution`. The reversal equation is a 10% soft overlay
calculated only from completed sessions before the
trade date. It does not bypass the SEC fundamentals gate or the paper-only
broker boundary.

## Locked operating rules

- AI, AI-infrastructure, and medical-AI public-stock universe
- most recent six months of completed price history (127 trading sessions)
- top 50 preferred; top 25 research view
- equal-weight allocation
- prior-data-only ranking; no following-period lookahead
- April trades only when the contemporaneous signal is positive
- voluntary simulated sales are blocked below each lot's recorded purchase price
- Fidelity tradability remains the live eligibility constraint
- Alpaca paper is broker/API connectivity, not the ranking engine

## Runtime layout

Run exactly **one Railway replica** and exactly **one Gunicorn worker**. The
monitor is an in-process background thread; multiple workers or replicas could
evaluate the same account concurrently. The Docker image fixes Gunicorn at one
worker, while Railway's service settings must remain at one replica with Serverless
sleep disabled. `railway.json` applies an always-restart policy.

Attach a Railway persistent volume at `/data`. These files must remain on that
volume:

- `/data/mosquito-runner-state.json` — start/stop and allocation state
- `/data/mosquito-simulation.json` — simulated basket state
- `/data/mosquito-engine.json` — peaks, protected triggers, and engine events

Without that volume, a redeploy or container replacement loses the persisted
state and peak history. A volume mount can replace image-time ownership, so the
container starts as root only long enough to repair `/data` ownership and then
drops to the unprivileged `mosquito` user before starting Gunicorn.

## Required Railway variables

Set secrets only in Railway **Variables**; never commit them.

| Variable | Required | Purpose |
|---|---:|---|
| `ALPACA_API_KEY` | Yes | Alpaca paper account key |
| `ALPACA_SECRET_KEY` | Yes | Alpaca paper account secret |
| `DASHBOARD_TOKEN` | Yes | Protects the owner dashboard and API |
| `ALPACA_ENABLE_ORDER_EXECUTION=true` | Yes for all paper broker orders | Allows submissions to the paper broker only; it cannot enable live mode. When false, automatic entries and rebalances are skipped while the local simulation remains available. |
| `MOSQUITO_ENABLE_SIMULATION=true` | Yes for the local V5.8 simulation | Enables the persistent simulated basket |
| `MOSQUITO_ENGINE_INTERVAL=5` | Recommended | Seconds between paper-account monitor cycles; minimum effective value is 2 |

`PORT` is supplied by Railway. The three `MOSQUITO_*_FILE` paths are already
fixed to `/data` by the image and normally should not be overridden. Credential
aliases remain supported for compatibility, but the names above are canonical.

Do not add a live-trading variable or substitute live Alpaca credentials. The
broker client is constructed with `paper=True` regardless of environment
variables.

## Health and readiness

- `GET /health` is the Railway health check. It means the HTTP process is alive;
  it deliberately does not depend on Alpaca, so a broker outage does not cause a
  restart loop.
- `GET /ready` is an operator diagnostic. Its JSON reports whether Alpaca keys
  are configured and whether the state directory is writable. In this version
  it still returns HTTP 200 when either diagnostic is false, so it must not be
  used as Railway's restart gate.
- Authenticated `GET /api/status`, `/api/account`, and `/api/engine` are the
  runtime checks for paper mode, broker reachability, and the monitor's latest
  cycle. Treat stale `last_cycle`, an engine `last_error`, or account failure as
  an operational alert—not as a reason to start a second replica.

## Deploy on Railway

1. Push the repository to GitHub and create a Railway service from it.
2. Attach one persistent Railway volume at `/data`.
3. Add the required variables listed above.
4. In Railway service settings, keep the service at one replica, disable
   Serverless sleep, and do not override the Docker start command.
5. Deploy, then verify `/health`, `/ready`, and the authenticated status,
   account, and engine endpoints.

The restart policy keeps the process available, but availability is not proof
that Alpaca is reachable or that an order filled. Verify broker state and paper
fills in both Mosquito and Alpaca.

Railway detects `railway.json`, installs `requirements.txt`, and starts the app.

## Important recovery note

The original executable V5.8 Master source and exact scoring weights were not
preserved. This package explicitly reconstructs the recovered feature family
and locked operating rules; it does not claim byte-for-byte identity with the
missing program. The scan/GO control generates a research watchlist. It does
not itself submit purchases. The trailing engine can submit protected paper
sell orders only when paper order execution is explicitly enabled.

Current restart limitation: the persisted `running` flag survives a restart,
but the in-process monitor is started by the Start Bot action. Until automatic
worker-start initialization is implemented and tested in `app.py`, press Start
Bot once after each Railway restart before relying on trailing monitoring.

## Teddy Fundamental Gate

Every published or tradable pick must first pass the fail-closed rules in
`TRADING_RULES.md`. Mosquito reads recent SEC company facts and filings to
require positive net assets/equity, debt coverage by cash or a clear asset
cushion, and positive backlog/order-book/RPO evidence. Missing evidence is a
failure; momentum cannot override it. Results are cached on the persistent
volume and attached to selected dashboard rows for auditability.
