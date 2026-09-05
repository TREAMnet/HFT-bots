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
