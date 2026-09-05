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

## Phase 1c: Dashboard controls — multi-pair + strategy management

**Goal:** Extend the working custom status page (`localhost:8600`) from
read-only monitoring into a lightweight control panel, so pairs and strategy
params can be tested without hand-editing config files or the CLI.

**Decided scope:**
- **Multi-pair setup:** open decision — evaluate whether to run one
  `simple_pmm`-style bot instance per pair (separate processes) or a single
  bot/strategy handling multiple pairs, and recommend based on whichever is
  simpler to implement and monitor given the current architecture. Note the
  trade-off either way in the write-up (e.g. per-pair isolation and easier
  individual restart vs. single-process simplicity).
- **Controls to expose in the dashboard:**
  - Add / remove trading pairs
  - Adjust strategy parameters: spread, order size, refresh time
  - Start / stop the bot(s)
  - (Explicitly out of scope for now: adjusting paper trade balances —
    current paper balances are sufficient for testing.)

> ⚠ **UI note:** the status page currently shows a BTC-USD price reference
> visual. Once multi-pair is live, this needs to extend to **every active
> pair**, not stay hardcoded to BTC-USD — each added pair should get its own
> price visual on the dashboard, and it should disappear/appear as pairs are
> removed/added.
- **Apply behavior: semi-automated (decided Sept 2026, see note below).**
  Dashboard writes the updated config and validates input as originally
  planned, but does **not** attempt to restart the bot process itself.
  Instead it surfaces the exact manual restart command for Jay to run.

> ⚠ **Decision note (Sept 2026):** Full automation was attempted (Task 9)
> and got most of the way there — `multi_pmm.py` (multi-pair script),
> control endpoints, and input validation all work and are committed. One
> real bug was found and fixed along the way (the status page's own
> background poller was sending a signal that could kill a freshly-restarted
> bot mid-startup — a race between the polling code and Hummingbot's engine
> startup). But even after that fix, the automatic "write config → restart →
> verify" flow still failed intermittently, with a second cause that stayed
> unresolved despite isolated single-variable testing — each test pointed at
> a different, sometimes contradictory culprit (classic flaky/race-condition
> signature). Switching the container to idle-host mode was tried and made
> things worse, not better. Rather than keep guessing or ship a control-panel
> UI on top of an unreliable restart mechanism, the decision was made to drop
> to this reduced, semi-automated version: config writing and validation
> (the verified, reliable parts) stay; automatic restart is replaced with a
> displayed manual command. Revisit full automation later only if this
> reduced version proves genuinely annoying to live with — not a current
> priority.

**Implementation notes / things to watch:**
- Since this now *writes* config and restarts processes (not just reads
  logs/status), validate inputs before applying (e.g. reject a duplicate
  pair, an invalid spread value) rather than letting a bad config crash the
  bot silently.
- A restart briefly interrupts the bot's live paper-trading loop — fine for
  this personal/testing context, but worth a small visible confirmation step
  in the dashboard ("Applying changes — bot restarting...") rather than a
  silent action, so it's clear when a change has taken effect.
- Keep this logic in the `HFT-bots` repo (same as the status page), not
  inside Hummingbot or hummingbot-api's own code, for the same
  upgrade-independence reason as Phase 1b.
- Document any new pairs/params added through the dashboard the same way
  Phase 1 was documented — so state is recoverable/inspectable outside the
  UI too (e.g. current live config still readable as a plain file, not only
  through the dashboard).

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
