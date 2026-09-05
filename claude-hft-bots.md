# HFT-bots — Reference Doc

## What This Is
Personal, hobby-scale exploration of algorithmic crypto trading using
[Hummingbot](https://github.com/hummingbot/hummingbot), an open-source
(Apache 2.0) framework for building market-making/arbitrage bots.
**Strictly separate from TREAM_net** — no crossover with TREAM_net branding,
pricing, or client scope. Purely for personal learning/testing.

- **Repo:** `https://github.com/TREAMnet/HFT-bots` — same KBO entity as
  TREAM_net for administrative purposes, but not part of the TREAM_net
  offering, not advertised on the TREAM_net site.
- **Structure:** this repo holds configs, setup notes, dashboard code, and
  (later) the Condor layer — **not** a clone/fork of Hummingbot itself.
  Hummingbot lives as its own separate local clone; its codebase/commit
  history stays out of this repo.
- **Build workflow:** same pattern as the TREAM_net website — Jay plans/
  decides in chat, Claude Code executes. Architectural forks and open
  decisions get discussed in chat first, then handed to Claude Code as a
  finalized spec.
- **Full task spec / handoff doc:** `hummingbot-setup-spec.md` (same
  project) — canonical source for phase-by-phase build instructions.

## Why Hummingbot
Chosen over alternatives (Freqtrade, Jesse, Passivbot, OctoBot, Gainium,
Superalgos, Zenbot, Kelp) because it's the strongest fit specifically for
market-making/arbitrage across both CEXs and DEXs, with institutional-grade
execution and a large, active connector ecosystem. Not revisited unless a
future project calls for a different strategy style (e.g. Freqtrade for
signal-based/discretionary strategies, which isn't Hummingbot's focus).

- **Cost:** completely free — open-source, no subscription. Only real costs
  are normal exchange trading fees and optional server hosting.
- **Non-custodial:** exchange API keys are encrypted locally; Hummingbot
  never takes custody of funds.
- **Scam awareness:** only use the official repos (`github.com/hummingbot`)
  and hummingbot.org — third parties impersonate "managed Hummingbot"
  services.

## Status (as of Sept 2026)

**Phase 1 — Core setup: done.**
- Hummingbot installed, `simple_pmm` script verified placing real simulated
  paper orders on `binance_paper_trade`.

**Phase 1b — Dashboard limitation: resolved via custom build.**
- The official hummingbot-api Dashboard's status/PnL panel only works for
  *controller*-based deploys, which hit an upstream bug
  (`PaperTradeExchange` has no `trading_rules`) that blocks paper orders
  entirely.
- *Script*-based deploys (what's actually running) trade correctly but
  register as "stopped" in the dashboard, since its status logic requires
  MQTT controller performance reports a plain script never sends.
- Decision: don't patch vendored Hummingbot source (too fragile against
  image updates for a personal project). Instead, built a **custom
  lightweight status page**, independent of the broken controller/MQTT
  path — live at `http://localhost:8600/`. Shows bot running state/uptime,
  active orders, fill history, and computed PnL (derived from trade
  history, since the dashboard's PnL field isn't available on this path).

**Phase 1c — Dashboard controls: landed on a reduced, semi-automated version.**
- `multi_pmm.py` (multi-pair strategy script) verified working with 2-3
  pairs trading correctly; control endpoints (add/remove pairs, adjust
  spread/order size/refresh time, start/stop) built with input validation,
  all committed.
- Full automation (dashboard writes config *and* restarts the bot itself)
  was attempted but hit an intermittent, unresolved second bug on top of
  one real bug that was found and fixed (a race between the status page's
  own polling and Hummingbot's engine startup). Isolated tests gave
  contradictory signals — classic flaky/race-condition territory, not
  something worth open-ended debugging time on a hobby project.
- **Decision (Sept 2026):** dropped to a reduced version — dashboard writes
  config and validates input (the reliable, verified parts) but surfaces a
  manual restart command instead of auto-restarting. The Task 11
  control-panel UI is being built on top of this reduced flow, not the
  flaky automated one. Full automation may be revisited later if the manual
  step proves annoying — not a current priority.
- Explicitly out of scope throughout: adjusting paper trade balances
  (current balances are sufficient for testing).

**Phase 2 — Condor (AI agent layer): not started, deliberately deferred.**
- Repo: `https://github.com/hummingbot/condor`
- Lets an LLM make trading decisions (entries/exits, param tuning) while
  Hummingbot executes; controlled via Telegram or its own web dashboard.
- Only pursue once Phase 1 (paper trading + monitoring/control) is fully
  verified and stable — avoids debugging two new systems at once.

## Known Open Item — Hosting
Currently runs locally; bot/dashboard only live while the laptop is on and
the process stays alive. Considered but not yet acted on:
- **Oracle Cloud "Always Free" tier** — best fit found: up to 4 vCPUs / 24 GB
  RAM (Ampere A1 ARM), 200 GB storage, 10 TB monthly transfer, no expiry.
  Caveats: ARM-capacity availability isn't guaranteed per region, no uptime
  SLA, idle instances can be reclaimed, opaque account approval process.
  Card required at signup but no charge within free-tier limits.
- AWS/Azure free tiers ruled out as too weak/time-limited (burstable
  micro instances, 12-month expiry) for continuous bot uptime.
- Not a current priority — revisit once local dashboard/controls (Phase 1c)
  are confirmed working well.

## Working Principles Carried Over From TREAM_net
- Never commit secrets: exchange API keys, Hummingbot's encrypted keystore,
  `.env` files, dashboard/Telegram tokens. `.gitignore` set up before first
  commit.
- Architectural forks (not just bugs) get surfaced and decided in chat
  before Claude Code proceeds — the Phase 1b dashboard decision is the
  reference example of this pattern.
- Jay is not a professional developer; prefers clear, readable
  implementations and being walked through *what* changed and *why*, not
  just handed a diff.

---
*Created: September 2026, alongside Phase 1c (dashboard controls) build.*
