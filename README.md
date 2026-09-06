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
  official Streamlit dashboard, which was removed. Also a semi-automated
  control panel — see "Control panel" below.
- `hummingbot-scripts/multi_pmm.py` — multi-pair PMM strategy script (source
  of truth, git-tracked here; copy to `~/hummingbot/scripts/` to run it).

## Start everything
```bash
cd ~/hummingbot && make deploy          # Hummingbot client (Docker)
docker cp hummingbot-scripts/multi_pmm.py hummingbot:/home/hummingbot/scripts/multi_pmm.py  # once per fresh container
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

## Control panel
`http://localhost:8600` also lets you add/remove trading pairs, edit
strategy parameters (spread, order amount, refresh time), and start/stop
the bot, without hand-editing YAML.

**Applying a pair/param change writes and validates the config file, but
does not restart the bot for you** — it shows a command instead:
```
hbot stop; HBOT_PASSWORD=$(cat ~/.hbot_keystore_password) hbot start conf_paper_bot.yml
```
Run that in your own terminal to pick up the change. This was a deliberate
scope decision, not an oversight — see `hummingbot-setup-spec.md` Phase 1c
for why full automation was tried and dropped. If that command fails with
`bot exited during startup`, just run it again — a known, occasionally
flaky gap in Hummingbot's own stop-then-start sequencing, unrelated to
this page.

The live config is always readable as plain YAML at
`~/hummingbot/conf/scripts/conf_paper_bot.yml` (or via `hbot config
--json`), independent of the control panel.

### Paper balances persist across restarts
Hummingbot re-seeds paper balances from `conf_client.yml`'s static defaults
on every `hbot start`, so without help every restart (including the manual
command above) would silently reset capital while `hbot history` PnL keeps
accumulating. `status_server.py` prevents this: a background thread writes
the live balances back into `conf_client.yml` every 60s
(`--checkpoint-interval` to change) and once more when you press **Stop**, so
the next start resumes where the last run left off. The page shows a
"Paper balances checkpointed …" line under Balances. No effect on live
trading — this is a paper-only mechanism. If the laptop sleeps or the
process is killed uncleanly you lose at most one interval of paper fills.

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
