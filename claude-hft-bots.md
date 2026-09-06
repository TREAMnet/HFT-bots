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

**Phase 1c — Dashboard controls: regression fixed (Sept 2026), three rounds.**
Was marked complete after verified testing, then broke, then took three
passes to fully resolve:
1. **UI-wipe bug:** the confirmation/error banner lived inside `#app`,
   which `refresh()` regenerates wholesale, wiping the message before it
   rendered. Fixed by moving those elements outside `#app`. Verified only
   via `curl` at the time — which is exactly why it didn't catch round 2.
2. **~5s hidden latency:** every control request called
   `read_current_config()`, which shelled out to `hbot config --json` — a
   4-5s CLI cold-start every single call, vs. ~0.2s for a plain file read.
   Fixed by reading the config file directly. Round trip: ~5s → ~0.3-0.5s.
3. **Poller wiping in-progress input (found via screen recording):** the
   *same* `#app.innerHTML` full-regeneration behind bug #1 was also
   recreating the Trading Pairs/Parameters inputs on every poll tick, so
   an existing `setIfIdle()` focus guard could never work — it was
   checking focus against a node that had just been replaced. Fixed by
   moving the whole Controls section outside `#app` too, so the guard now
   checks a persistent node. Verified with a real DOM (Node + jsdom)
   driving the live page's JS and simulating focus/typing across repeated
   poll ticks — curl can't see this class of bug at all, which is why
   round 1's verification missed it.

What shipped as part of the Phase 1c build (for reference):
- Control panel: add/remove pairs, edit spread/order-size/refresh-time,
  Start/Stop, input-validated.
- Per-pair price badges replacing the old hardcoded BTC-USDT reference.
- Semi-automated apply: config written + validated, manual restart command
  displayed rather than auto-restart.
- Earlier (separate, already-fixed) intermittent restart issue: the status
  page's background poller was sending a signal (`hbot status`) that could
  kill a freshly-restarting bot before it finished connecting. Fixed by
  checking process age instead of signaling during that window.
- Explicitly out of scope throughout: adjusting paper trade balances.

**Also flagged (Sept 2026): zero trades to date.** No fills have occurred
since Phase 1c began, only resting orders (order placement was verified
separately from fills). Suspected but unconfirmed cause: the spread has
been stuck at its default (visible in the UI as 0.001/0.1%) for the whole
testing period, since the control that would adjust it was the very thing
broken by the bugs above. Now that all three Phase 1c bugs are fixed and
spread edits actually persist through the UI, this is testable — not yet
tested. Next step: narrow the spread via the control panel and confirm
whether fills start happening, rather than continuing to assume the
connection between the two issues.

**Considered and set aside: jumping to Phase 2 (Condor) as a workaround.**
Discussed Sept 2026 — decided against for now. Condor sits on top of the
same `hummingbot-api` whose paper-trading path has a known upstream bug
(Phase 1b's `PaperTradeExchange`/`trading_rules` issue), so it may not
sidestep dashboard/status problems at all. Condor is also a genuine
strategy pivot (AI-driven decisions vs. fixed params), not just an
alternative dashboard — the original spec deferred it specifically to
avoid debugging two systems at once, which still applies. Revisit only
once Phase 1c's current bug is resolved and trading is confirmed working.

**Phase 1e — Persist paper balances across restarts: done (Sept 2026).**
Hummingbot re-seeds paper balances from `conf_client.yml` defaults on every
`hbot start`, so each restart (needed for every control-panel change) reset
capital while `hbot history` PnL kept accumulating. `status_server.py` now
runs a background thread that checkpoints the live balances back into
`conf_client.yml` every 60s and on Stop, via `hbot config`. Not tied to any
restart trigger, so a manual `hbot stop; hbot start` is covered like the
Stop button. Verified with a real stop/start: balances resumed from the
checkpoint, not the defaults. See `hummingbot-setup-spec.md` Phase 1e.

**Phase 1 overall: fully verified working again** — paper trading,
monitoring, and multi-pair controls (including in-progress input surviving
the background poller) all confirmed working after the three-round Phase 1c
regression fix above. Still open: the zero-fills question flagged above.

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
