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

## Phase 1c: Dashboard controls — multi-pair + strategy management (regression fixed — see below)

> ✅ **Regression resolved (Sept 2026), in three rounds.** Phase 1c was
> marked complete after verified testing, then broke, then took three
> passes to fully fix — each prior "fix" was real but incomplete, so it's
> worth recording why each one wasn't the full story:
>
> 1. **UI-wipe bug.** The confirmation/error banner lived inside `#app`,
>    which `refresh()` regenerates wholesale — wiping the message before
>    it rendered. Fixed by moving those elements outside `#app`. Verified
>    only via `curl` at the time, which is exactly why it didn't catch
>    round 2.
> 2. **~5s hidden latency.** Every control request called
>    `read_current_config()`, which shelled out to `hbot config --json` —
>    a ~4-5s CLI cold-start on every single call, vs. ~0.2s for a plain
>    `docker exec ... cat` of the same file. Fixed by reading the config
>    file directly. Round trip: ~5s → ~0.3-0.5s.
> 3. **Poller wiping in-progress input (found via screen recording,
>    Sept 5, 2026).** Despite both fixes above, typing into any control
>    field was still getting silently wiped within ~2-3s, before any
>    button was clicked. Root cause: the *same* `#app.innerHTML` full
>    regeneration from bug #1 was also destroying and recreating the
>    Trading Pairs and Parameters inputs on every poll tick (not just
>    control actions) — including the one under the user's cursor. A
>    `setIfIdle()` focus guard already existed and looked correct, but
>    could never work: it checked `document.activeElement !== el` against
>    a node that had just been replaced, so the "currently focused"
>    element could never match. The `newPair` field had no guard at all.
>    Fixed by moving the entire Controls section outside `#app`, same
>    pattern as bug #1 — the input nodes now persist across refreshes, so
>    the focus check is actually checking something real.
>
> Round 3 was verified differently on purpose, after round 1's curl-only
> verification proved insufficient: a real DOM (Node + jsdom) driving the
> actual served page's JS against the live server, simulating focus and
> typing exactly like a browser, sampled across repeated poll ticks. That
> caught what curl structurally cannot see — what's rendered, when, and
> whether a specific DOM node survives a refresh.

**Status: done and verified (Sept 2026).** Control panel live at
`http://localhost:8600`.

**Shipped:**
- Add/remove trading pairs, edit spread/order-size/refresh-time, Start/Stop
  — all with input validation.
- Per-pair price badges (replacing the old single hardcoded BTC-USDT
  reference) — extends automatically as pairs are added/removed.
- Semi-automated apply per the Sept 2026 decision below: config is written
  and validated, then the exact restart command is displayed for Jay to run
  manually rather than auto-restarting.

**Root cause of the earlier intermittent restart failures — found and
fixed:** `hbot status` sends the running engine a signal to request a
snapshot. The engine only handles that signal safely *after* it finishes
connecting to the exchange — before that, the signal kills the process
outright. The status page's own background poller was sending exactly that
signal during every restart's connection window, regardless of whether the
restart was triggered by dashboard code or run manually in the terminal.
Fix: the poller now checks the bot process's age (a plain file read, no
signal sent) and backs off during that startup window. Confirmed via
repeated before/after testing.

**Known remaining flakiness (separate, minor, no action needed):** a small
amount of flakiness remains in Hummingbot's own stop-then-start sequencing,
unrelated to the fix above. This is exactly the "just run it again" scenario
the semi-automated (not fully automated) decision below already accounts
for.

<details>
<summary>Original goal and decision history (for context)</summary>

**Goal:** Extend the working custom status page (`localhost:8600`) from
read-only monitoring into a lightweight control panel, so pairs and strategy
params can be tested without hand-editing config files or the CLI.

- **Multi-pair setup:** Claude Code's call — implemented as verified working
  with 2-3 pairs via `multi_pmm.py`.
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

> ✅ **UI note (added Sept 2026, resolved Sept 2026): amounts now display in
> USDT, not base asset.** The active orders list and the "Order amount"
> control field previously showed quantities in the pair's base asset (e.g.
> BTC for BTC-USDT), which made cross-pair comparison meaningless once
> multiple pairs were active (0.01 BTC vs. 0.1 ETH aren't comparable at a
> glance; $50 USDT is). Confirmed by Jay's observation.
>
> This wasn't just a label swap — `multi_pmm.py`'s `order_amount` field
> changed from one shared `Decimal` (applied identically in base-asset units
> to every pair) to a `Dict[str, Decimal]` keyed by trading pair, so each
> pair can actually carry its own base-asset amount:
> - **Active Orders table:** Amount column now shows `amount × that pair's
>   live price` (the same price feeding the price badges), formatted as USDT.
> - **Order amount control:** renamed "Order amount (USDT)"; the user types a
>   USDT target, and the dashboard converts it to a base-asset amount per
>   active pair (using each pair's live price) before writing
>   `conf_paper_bot.yml` — Hummingbot's `order_amount` still needs base-asset
>   numbers under the hood. A live preview under the field shows the
>   resulting per-pair base-asset quantities as the user types, so it's clear
>   the same $ value produces different BTC/ETH/SOL/DOGE quantities.
> - Adding a new pair before ever resubmitting an amount seeds its
>   base-asset amount from an existing pair's raw number (no price history
>   exists yet for a pair that was just added) — a reasonable placeholder
>   until the next "Order amount" submission re-converts every active pair
>   at its actual price.
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

</details>

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
