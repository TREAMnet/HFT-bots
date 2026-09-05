# Hummingbot Setup — Task Spec for Claude Code

## Context
Setting up [Hummingbot](https://github.com/hummingbot/hummingbot), an open-source crypto trading bot framework, for local use. Goal is a safe, verifiable setup: paper trading first, with a visual dashboard to monitor bot activity, before any live exchange connection.

---

## Repo & Secrets

- **Repo:** `https://github.com/TREAMnet/HFT-bots` — personal use only, separate from
  the TREAM_net business (no crossover with TREAM_net branding, pricing, or client scope).
- **Structure:** this repo holds configs, setup notes, dashboard config, and (later)
  the Condor layer — **not** a clone or fork of Hummingbot itself. Hummingbot lives
  as its own separate local clone (per Phase 1 step 1), so its commit history and
  codebase stay out of this repo. Reference it by path/instructions in notes here,
  don't vendor it in.
- **Secrets — never committed, ever:**
  - Exchange API keys (paper trading needs none, but this applies once live keys exist)
  - Hummingbot's encrypted keystore / password
  - Any `.env` files, dashboard auth tokens, or Telegram bot tokens (relevant once Condor is added in Phase 2)
- Add a `.gitignore` to this repo **before the first commit**, covering at minimum:
  `.env*`, `*.key`, `conf/`, `keystore*`, and any local Hummingbot data/log paths if
  they're ever referenced from within this repo.

---

## Phase 1: Core Setup (this task)

### 1. Clone and install Hummingbot
- Repo: `https://github.com/hummingbot/hummingbot`
- Prefer the **Docker path** if Docker is available (`make setup` → `make deploy`) since it's more portable.
- Fall back to source install via conda/miniconda (`make install`) if Docker isn't available.
- Confirm `hbot --help` runs successfully before proceeding.

### 2. Verify with paper trading (no API keys, no real funds)
Run the `simple_pmm` strategy against `binance_paper_trade`:
```
hbot create simple_pmm --name conf_paper_bot.yml \
     --set exchange=binance_paper_trade --set trading_pair=BTC-USDT
hbot start conf_paper_bot.yml
hbot status
```
Confirm it places simulated orders and produces status output.

### 3. Set up the Hummingbot Dashboard
- Repo: `https://github.com/hummingbot/hummingbot-api`
- Install and run alongside the bot instance so it connects to the running bot(s).
- Confirm I can view bot status, active orders, and PnL in the browser.

### 4. Document the workflow
Write a short README or notes file covering:
- How to start/stop the bot
- How to launch the dashboard
- How to check status
So the setup can be repeated without re-deriving the steps.

### 5. Hard stop
**Do not connect real exchange API keys or move to live trading in this phase.** That happens later, together, once paper trading and the dashboard are confirmed working.

---

## Phase 1b: Dashboard limitation — decision & lightweight status page

**Findings (confirmed via logs, Sept 2026):**
- Controller-based deploy (what hummingbot-api's dashboard Deploy page uses by
  default) is what the dashboard's status/PnL panel is built for, but it hits an
  upstream Hummingbot bug: `PaperTradeExchange object has no attribute
  'trading_rules'`. Bot reports "running" but every order-placement attempt
  throws that AttributeError — it never actually places a paper order.
- Script-based deploy (`simple_pmm.py`, same script proven working in Phase 1
  step 2) trades correctly — placing and refreshing real simulated orders — but
  hummingbot-api's "running" status is hardcoded to require controller
  performance reports over MQTT, which a plain script never sends. So it will
  always show as "stopped" with an empty PnL panel in the dashboard, even
  though it's genuinely trading.
- Ruled out: patching the vendored `PaperTradeExchange`/`trading_rules` bug
  inside the Docker image. Too invasive for a personal project — an image
  update could silently re-break it, requiring a full re-diagnosis each time.

**Decision:** Keep the working script bot (`simple_pmm.py`) running as-is.
Treat the official hummingbot-api dashboard as **unreliable for this setup**
and stop relying on it for status/PnL — do not spend further effort making
controller-based paper trading work on the current hummingbot-api version.

**Next step — build a lightweight custom status page instead of the full
dashboard:**
- Purpose: just show what the bot is doing (not full PnL analytics, not the
  Hummingbot Dashboard's feature set).
- Source of truth — check both, use whichever is more reliable:
  1. hummingbot-api's generic container/instance endpoints (running/uptime/raw
     logs), if they work independently of the broken controller-performance
     panel.
  2. Tailing/parsing the script bot's own log output (order placements,
     cancellations, fills, balances) if the API doesn't expose this cleanly.
- Minimum content: is the bot running + uptime, current active orders
  (price/size), recent fills/trade history, and PnL.
  - PnL isn't reported by the script bot the way the dashboard's controller
    panel expects it (that's the MQTT path that's broken/unavailable here) —
    so this likely needs to be computed rather than read off an existing
    field: derive it from the fill/trade history (entry vs. exit prices,
    realized PnL on closed trades) plus current position vs. current market
    price for unrealized PnL. Confirm what data is actually available in the
    log/API output before deciding the exact calculation.
- Simple local web view, auto-refreshing on a short poll interval — no need
  for anything elaborate.
- Lives in this repo (`HFT-bots`), not inside the Hummingbot or hummingbot-api
  codebases — keeps it independent of upstream image updates.

---

## Phase 2 (later, separate task): Condor — AI agent layer
- Repo: `https://github.com/hummingbot/condor`
- Condor is an AI agent harness that sits on top of the Hummingbot API — it lets an LLM make trading decisions (entries/exits, parameter adjustments) while Hummingbot executes the actual trades.
- Controlled via Telegram or its own web dashboard.
- **Not in scope yet.** Only pursue once Phase 1 (paper trading + dashboard) is fully verified and stable.

---

## Notes
- Hummingbot is free/open-source (Apache 2.0) — no subscription. Costs are limited to normal exchange trading fees and optional server hosting.
- Non-custodial: exchange API keys are encrypted locally; Hummingbot never takes custody of funds.
- Be alert to third-party sites/services impersonating "managed Hummingbot" offerings — only use the official GitHub repos and hummingbot.org.
