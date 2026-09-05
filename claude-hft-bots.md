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

**Phase 1c — Dashboard controls: regression found and fixed (Sept 2026).**

Two separate root causes, not one shared crash:
1. **`http://localhost:8600/` stopped loading:** `status_server.py` was
   running as a plain foreground process with nothing supervising it, and
   the `hummingbot` bot container had no Docker restart policy (`no`, vs.
   `unless-stopped` on the other three service containers). When the host
   restarted, `hummingbot-api`/postgres/broker came back on their own but
   the bot container and the status page did not — confirmed via
   `docker inspect` (bot container `Exited (143)`, i.e. SIGTERM, with no
   error/shutdown log entries — an external kill, not a crash) and the
   status page process being entirely absent (no PID, nothing on
   port 8600). Fixed by giving the `hummingbot` container
   `--restart unless-stopped` (matching its siblings) and running
   `status_server.py` as a `systemd --user` service
   (`hft-status-page.service`, `Restart=on-failure`, lingering enabled) so
   both now survive reboots and crashes without manual relaunching.
2. **Control buttons no-op'd — two causes, not one.** A first fix (moving
   the confirmation/error banner out of the `#app` div that `refresh()`
   regenerates on every poll, which was wiping the message before it
   rendered) was real but insufficient — Jay reported the symptom
   persisting. Re-investigated with a real DOM (Node + jsdom driving the
   actual served page against the live server, not just curl) and found a
   second cause stacked on top: every control POST calls
   `read_current_config()` first, which shelled out to `hbot config
   --json` — costing ~4-5s per call (cold Python-interpreter startup)
   vs. ~0.2s for a plain `docker exec ... cat` of the same file. That
   made every action take ~5s with no progress indication beyond a
   static label, reading as a hang. Fixed by reading the config file
   directly instead of through the `hbot` CLI. Round trip is now
   ~0.3-0.5s, confirmation renders promptly and stays visible.

What shipped as part of the original Phase 1c build (for reference):
- Control panel: add/remove pairs, edit spread/order-size/refresh-time,
  Start/Stop, input-validated.
- Per-pair price badges replacing the old hardcoded BTC-USDT reference.
- Semi-automated apply: config written + validated, manual restart command
  displayed rather than auto-restart.
- Root cause of the *earlier* (separate, already-fixed) intermittent
  restart issue: the status page's background poller was sending a signal
  (`hbot status`) that could kill a freshly-restarting bot before it
  finished connecting to the exchange. Fixed by checking process age
  instead of signaling during that window.
- Explicitly out of scope throughout: adjusting paper trade balances.

**Phase 1 overall: fully verified working again** — paper trading,
monitoring, and multi-pair controls all confirmed live after the Phase 1c
regression fix above.

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
