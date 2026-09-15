# Mosquito V5.8 Master

Upload-ready, Railway-compatible reconstruction of the locked V5.8 Master dashboard.

## Locked operating rules

- AI / AI-infrastructure public-stock universe
- minimum four years of price history
- top 50 preferred; top 25 research view
- equal-weight allocation
- prior-data-only ranking; no following-period lookahead
- April trades only when the contemporaneous signal is positive
- Fidelity tradability remains the live eligibility constraint
- Alpaca is paper/API connectivity, not the ranking engine

## Deploy on Railway

1. Upload every file in this folder to one GitHub repository.
2. In Railway, choose **Deploy from GitHub repo** and select it.
3. Add `ALPACA_API_KEY` and `ALPACA_SECRET_KEY` under Railway **Variables** only if paper-account display is wanted.
4. Never commit real keys to GitHub.

Railway detects `railway.json`, installs `requirements.txt`, and starts the app.

## Important recovery note

The original executable V5.8 Master source and exact scoring weights were not preserved. This package explicitly reconstructs the recovered feature family and locked operating rules; it does not claim byte-for-byte identity with the missing program. The GO control generates a research watchlist. It does not submit orders.
