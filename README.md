# HFT-bots — Hummingbot Paper-Trading Setup

Personal Hummingbot setup notes. This repo holds configs/notes/status-page
code only — Hummingbot itself lives in separate clones outside this repo.

## Layout
- `~/hummingbot` — Hummingbot client (Docker), driven via the `hbot` CLI.
  This is the bot that actually trades — created/started in Phase 1 step 2.
- `~/hummingbot-api` — backend API + Postgres + EMQX broker (Docker
  Compose). Kept running for its public market-data endpoint and for
  Phase 2; its own dashboard/controller-based bot deployment is NOT used
  (see Phase 1b in `hummingbot-setup-spec.md` for why).
- `status-page/status_server.py` — this repo's own lightweight status page,
  reads the `hummingbot` container's `hbot` CLI directly. Replaces the
  official Streamlit dashboard, which was removed.

## Start everything
```bash
cd ~/hummingbot && make deploy          # Hummingbot client (Docker)
cd ~/hummingbot-api && make deploy      # API + Postgres + EMQX (optional, for market price)
HBOT_PASSWORD=$(cat ~/.hbot_keystore_password) hbot start conf_paper_bot.yml
cd status-page && python3 status_server.py   # http://localhost:8600
```

## Stop everything
```bash
hbot stop
cd ~/hummingbot-api && make stop
cd ~/hummingbot && docker compose down
# Ctrl+C the status_server.py process
```

## Check status
- Web: open `http://localhost:8600` — running badge, active orders vs.
  live market price, balances, trade history/PnL, auto-refreshing.
- CLI: `hbot status` / `hbot status --json` / `hbot history`
- API (Swagger only, not a real dashboard): `http://127.0.0.1:8000/docs`

## Create a new paper-trading bot via the CLI
```bash
hbot create simple_pmm --name conf_paper_bot.yml \
     --set exchange=binance_paper_trade --set trading_pair=BTC-USDT
HBOT_PASSWORD=$(cat ~/.hbot_keystore_password) hbot start conf_paper_bot.yml
```

## Known limitation
hummingbot-api's own dashboard/controller-based bot deployment doesn't work
for paper trading on this version — controller-based bots hit an upstream
bug (`PaperTradeExchange` has no attribute `trading_rules`) that stops them
from ever placing an order, and the working script-based bot doesn't report
through hummingbot-api's MQTT/controller status pipeline. See
`hummingbot-setup-spec.md` Phase 1b for the full root-cause writeup. Use
`status-page/` instead of the official dashboard.

## Secrets
Never in this repo. Hummingbot's keystore password lives in
`~/.hbot_keystore_password`; hummingbot-api's credentials live in
`~/hummingbot-api/.env`. `.gitignore` here covers `.env*`, `*.key`,
`conf/`, `keystore*` as a backstop, but the real rule is: don't put them
here at all.

## Phase 2 (not started)
Condor (AI agent layer) is deliberately out of scope until this phase is
fully verified and stable — see `hummingbot-setup-spec.md`.
