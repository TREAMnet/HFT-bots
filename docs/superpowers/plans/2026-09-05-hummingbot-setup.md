# Hummingbot Paper-Trading + Dashboard Setup Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Stand up Hummingbot locally in Docker, prove paper trading works via the CLI, and hand-wire the Streamlit Dashboard to a hummingbot-api backend so bot status/orders/PnL are viewable in a browser — no real exchange keys, no live trading.

**Architecture:** Three independently-run Docker stacks, each cloned outside this repo: (1) `hummingbot` itself, run via its own `make setup`/`make deploy`, driven by the `hbot` CLI for a one-off paper-trading sanity check; (2) `hummingbot-api`, which owns Postgres + an EMQX MQTT broker and orchestrates bot containers via its own Docker-socket mount; (3) the `hummingbot/dashboard` Streamlit image, run manually (not via the now Condor-only `deploy` repo installer) with `--network host` and env vars pointed at hummingbot-api's loopback port, since the two projects' default docker-compose files don't share a network. This repo (`hft-bots`) only ever holds notes/config, never the Hummingbot codebase itself.

**Tech Stack:** Docker Engine + Compose v2 (Debian `docker.io`/`docker-compose-v2` packages), Hummingbot (Docker image, orchestrated by its own Makefile), hummingbot-api (Docker Compose: API + Postgres 16 + EMQX 5), hummingbot/dashboard (Streamlit, prebuilt Docker image), git.

**Spec:** `hummingbot-setup-spec.md` (repo root) — Phase 1 only. Phase 2 (Condor) is explicitly out of scope; see spec for rationale.

**Environment notes locked in during planning (do not re-litigate without new evidence):**
- Host is a Crostini (ChromeOS Linux) container, Debian 13 "trixie", 2 vCPU, 2.7GB RAM, 17GB free disk, no swap, sudo passwordless, systemd present, network egress confirmed working.
- User decision: add a swap file and proceed on this machine (not a bigger box).
- `hummingbot/deploy`'s `setup.sh` (current `main`) only supports deploying Condor or the bare `hummingbot-api` stack — it does **not** wire up the Streamlit dashboard anymore, despite the dashboard repo's own README still recommending that path. User decision: hand-wire dashboard + API ourselves rather than fall back to Swagger-only or pull Condor forward (Condor is an AI trading-decision agent, not a monitoring tool, and pulling it into Phase 1 would violate the spec's own phase-gate rationale).

## Global Constraints

- This repo (`hft-bots`) holds configs, setup notes, dashboard config, and (later) Condor — **never** a clone/fork of Hummingbot itself; Hummingbot lives in its own separate local clone.
- No crossover with TREAM_net branding, pricing, or client scope.
- Never commit: exchange API keys, Hummingbot's encrypted keystore/password, any `.env` files, dashboard auth tokens, Telegram bot tokens.
- `.gitignore` must exist in this repo **before the first commit**, covering at minimum: `.env*`, `*.key`, `conf/`, `keystore*`, and any local Hummingbot data/log paths ever referenced from within this repo.
- **Hard stop:** do not connect real exchange API keys or move to live trading in this phase.
- Phase 2 (Condor) is out of scope for every task below.

---

### Task 1: Add a swap file

**Files:** none (host-level change only)

- [x] **Step 1: Confirm current state**

Run: `free -h`
Expected: `Swap:` row shows `0B` total (confirms nothing to lose by proceeding).

- [x] **Step 2: Create and enable a 4GB swap file**

Root filesystem here is **Btrfs**, not ext4 — plain `fallocate`/`dd` + `mkswap` fails with `swapon: Invalid argument` because Btrfs swap files need NOCOW and no compression set before any data is written. Use the dedicated helper instead:

```bash
sudo btrfs filesystem mkswapfile --size 4g /swapfile
sudo swapon /swapfile
```

- [x] **Step 3: Persist across container restarts**

```bash
grep -q '^/swapfile' /etc/fstab || echo '/swapfile none swap sw 0 0' | sudo tee -a /etc/fstab
```

- [x] **Step 4: Verify**

Run: `free -h`
Expected: `Swap:` row now shows `4.0Gi` total, `0B` used.

(No git commit — no repo file changed.)

---

### Task 2: Initialize git repo + `.gitignore`

**Files:**
- Create: `/home/jeffreyianbogaerts/hft-bots/.gitignore`

**Interfaces:**
- Produces: an initialized git repo at `hft-bots/` that every later task's commits land in.

- [x] **Step 1: Initialize the repo**

Run: `git init` (from `/home/jeffreyianbogaerts/hft-bots`)
Expected: `Initialized empty Git repository in .../hft-bots/.git/`

- [x] **Step 2: Write `.gitignore`**

```
# Secrets — never commit these
.env
.env.*
*.key
keystore*

# Hummingbot config/data if ever referenced from this repo
conf/
data/
logs/

# OS/editor noise
.DS_Store
*.swp
```

- [x] **Step 3: Verify status**

Run: `git status`
Expected: `.gitignore` and `hummingbot-setup-spec.md` listed as untracked (new) files; no ignored paths shown. `CLAUDE.md` is deliberately left out of this task — see the note in the final summary; do not commit it here.

- [x] **Step 4: Commit**

```bash
git add .gitignore hummingbot-setup-spec.md
git commit -m "chore: initialize repo with .gitignore before first commit"
```

---

### Task 3: Install Docker Engine + Compose

**Files:** none (host packages only)

- [x] **Step 1: Update apt and install Docker from Debian's own repos**

Debian trixie is very new — use the in-distro packages rather than Docker's own apt repo, which may not yet publish a `trixie` channel. Note: the package is called `docker-compose` in Debian's repos, not `docker-compose-v2` — but it's actually Compose v2.26.1 (wires up as the `docker compose` plugin), not legacy v1:

```bash
sudo apt-get update
sudo apt-get install -y docker.io docker-compose docker-buildx uidmap
```

- [x] **Step 2: Enable and start the daemon**

```bash
sudo systemctl enable --now docker
```

Expected: `systemctl status docker` shows `active (running)`.

- [x] **Step 3: Let your user run docker without sudo**

```bash
sudo usermod -aG docker "$USER"
```

Group membership needs a new login session to take effect. For the rest of this plan, either open a fresh shell, or use `sg docker -c '<command>'` to run a single command with the new group applied without logging out.

- [x] **Step 4: Verify Docker works end-to-end (daemon + network pull)**

Run: `sg docker -c 'docker run --rm hello-world'`
Expected: output includes `Hello from Docker!` — confirmed, including the image pull over the network.

(No git commit — no repo file changed.)

---

### Task 4: Clone and build Hummingbot, verify the `hbot` CLI

**Files:** none in `hft-bots` — Hummingbot clones to `~/hummingbot`, outside this repo per the spec.

- [x] **Step 1: Clone**

```bash
git clone https://github.com/hummingbot/hummingbot.git ~/hummingbot
```

- [x] **Step 2: Docker setup**

```bash
cd ~/hummingbot
echo n | sg docker -c 'make setup'
```

Answered **`n`** to the "Include Gateway?" prompt (DEX middleware) — we're only paper-trading against a centralized exchange (`binance_paper_trade`), Gateway isn't needed, and skipping it saves RAM on this box. (`sg docker -c '...'` is needed for every docker command this session since group membership from Task 3 doesn't apply until a fresh login shell.)

- [x] **Step 3: Deploy the container**

```bash
sg docker -c 'make deploy'
```

Pulls `hummingbot/hummingbot:latest` (prebuilt image, no local build) and starts it with `network_mode: host`.

- [x] **Step 4: Link the `hbot` CLI onto the host PATH**

```bash
sg docker -c 'make link-cli'
```

Linked to `~/.local/bin/hbot` — make sure that's on `PATH`.

- [x] **Step 5: Verify**

Run: `sg docker -c 'hbot --help'`
Expected: prints Hummingbot's CLI usage/help text (confirms the wrapper reaches the running container) — confirmed.

(No git commit — Hummingbot's codebase never enters `hft-bots`.)

---

### Task 5: Verify paper trading (simple_pmm on binance_paper_trade)

**Files:** none in `hft-bots`.

**Interfaces:**
- Consumes: working `hbot` CLI from Task 4.

- [x] **Step 1: Create the strategy config**

`simple_pmm` is a V2 script (`scripts/simple_pmm.py` in the image) with defaults for everything except exchange/trading_pair (`order_amount=0.01`, `bid_spread`/`ask_spread=0.001`, `order_refresh_time=15`, `price_type=mid`), so no further prompts appear:

```bash
sg docker -c "$HOME/.local/bin/hbot create simple_pmm --name conf_paper_bot.yml \
     --set exchange=binance_paper_trade --set trading_pair=BTC-USDT"
```

Expected/confirmed: `## created v2-script/conf_paper_bot.yml` with `ready: yes`.

- [x] **Step 2: Start it**

First attempt fails with `Error: no password provided (code 4)` — Hummingbot encrypts its local keystore with a password even for paper trading (no exchange keys needed, but the client itself is protected; this is exactly the "Hummingbot's encrypted keystore / password" secret the spec's Secrets section calls out). Generate one, store it **outside this repo**, and pass it via `HBOT_PASSWORD`:

```bash
HBOT_PASSWORD=$(openssl rand -base64 24)
echo "$HBOT_PASSWORD" > ~/.hbot_keystore_password
chmod 600 ~/.hbot_keystore_password
sg docker -c "HBOT_PASSWORD='$HBOT_PASSWORD' $HOME/.local/bin/hbot start conf_paper_bot.yml"
```

Expected/confirmed: `## start` — `status: running`.

- [x] **Step 3: Confirm simulated activity**

```bash
sg docker -c "$HOME/.local/bin/hbot status"
```

Expected: status output shows the strategy running, simulated balances for the paper-trade account, and active buy/sell orders around the current BTC-USDT market price. Confirmed — e.g. `1 BTC` / `100000 USDT` simulated balances, live buy/sell orders at ~$79.6-79.8k around the real BTC-USDT mid price.

- [x] **Step 4: Stop it to free RAM before the next tasks**

```bash
sg docker -c "$HOME/.local/bin/hbot stop"
```

Expected: `## stop` — `stopped: yes`; a follow-up `hbot status` reports `state: stopped`. Confirmed.

(No git commit — this is a runtime verification, not a repo change.)

---

### Task 6: Deploy the hummingbot-api backend

**Files:** none in `hft-bots` — clones to `~/hummingbot-api`. Its `.env` (created by `make setup`) holds real secrets and must never be copied into this repo.

- [x] **Step 1: Clone**

```bash
git clone https://github.com/hummingbot/hummingbot-api.git ~/hummingbot-api
cd ~/hummingbot-api
```

- [x] **Step 2: Run setup**

`setup.sh`'s prompts use `/dev/tty` when stdin isn't a terminal, which would hang a non-interactive run — detach from the controlling terminal with `setsid` first so it falls back to reading stdin directly, and feed the answers (username, password, password-confirm, config-password, config-password-confirm, tailscale-answer) that way:

```bash
API_USERNAME="admin"
API_PASSWORD=$(openssl rand -base64 18 | tr -d '/+=')
CONFIG_PASSWORD=$(openssl rand -base64 18 | tr -d '/+=')
{
  echo "HUMMINGBOT_API_USERNAME=$API_USERNAME"
  echo "HUMMINGBOT_API_PASSWORD=$API_PASSWORD"
  echo "HUMMINGBOT_API_CONFIG_PASSWORD=$CONFIG_PASSWORD"
} > ~/.hummingbot-api-credentials
chmod 600 ~/.hummingbot-api-credentials

cd ~/hummingbot-api
sg docker -c 'setsid ./setup.sh' <<EOF
$API_USERNAME
$API_PASSWORD
$API_PASSWORD
$CONFIG_PASSWORD
$CONFIG_PASSWORD
n
EOF
```

Tailscale answered **`n`** — not needed for local-only personal use. Confirmed: `.env created successfully!`, mode `600`.

- [x] **Step 3: Deploy**

```bash
sg docker -c 'make deploy'
```

- [x] **Step 4: Verify all three containers are up**

Run: `sg docker -c 'docker ps --format "{{.Names}}\t{{.Status}}"'`
Expected: three lines for `hummingbot-api`, `hummingbot-broker`, `hummingbot-postgres`, all showing `Up` (and `healthy` shortly after). Confirmed — also confirmed the swap file from Task 1 is already in active use (`Swap: 199Mi` used) running all four containers (including the Task 5 `hummingbot` one) at once on this 2.7GB box.

- [x] **Step 5: Verify the API answers**

Run: `curl -s -o /dev/null -w '%{http_code}\n' http://127.0.0.1:8000/docs`
Expected: `200`. Confirmed.

(No git commit — secrets live in `~/hummingbot-api/.env`, outside this repo.)

---

### Task 7: Deploy the Streamlit dashboard, hand-wired to hummingbot-api

**Files:** none in `hft-bots` (runtime container only).

**Interfaces:**
- Consumes: hummingbot-api listening on `127.0.0.1:8000` from Task 6, and the API username/password chosen in Task 6 Step 2.

- [x] **Step 1: Run the dashboard container with host networking**

The dashboard's own default `docker-compose.yml` expects a service named `backend-api` on a shared Compose network, which doesn't exist here since hummingbot-api was deployed as its own separate stack. Use `--network host` instead so the dashboard container can reach the API at `127.0.0.1:8000` directly:

```bash
source ~/.hummingbot-api-credentials
sg docker -c "docker run -d --name hummingbot-dashboard \
  --network host \
  --restart unless-stopped \
  -e AUTH_SYSTEM_ENABLED=False \
  -e BACKEND_API_HOST=localhost \
  -e BACKEND_API_PORT=8000 \
  -e BACKEND_API_USERNAME='\$HUMMINGBOT_API_USERNAME' \
  -e BACKEND_API_PASSWORD='\$HUMMINGBOT_API_PASSWORD' \
  hummingbot/dashboard:latest"
```

- [x] **Step 2: Verify it's serving**

Run: `curl -s -o /dev/null -w '%{http_code}\n' http://127.0.0.1:8501`
Expected: `200`. Confirmed — RAM held (swap absorbed the extra load: 481Mi swap used, 1.0Gi still available, all four containers plus this one running).

- [ ] **Step 3: Open it in the browser**

Open `http://localhost:8501` in the Chromebook's Chrome browser (Crostini forwards container ports to ChromeOS automatically — same as any other `localhost` port from this container).
Expected: the Hummingbot Dashboard UI loads without an auth prompt (since `AUTH_SYSTEM_ENABLED=False`).

- [ ] **Step 4: Deploy a bot through the dashboard (this is what makes it visible)**

hummingbot-api orchestrates bots itself via its mounted Docker socket — it doesn't automatically pick up the standalone bot from Task 5. In the dashboard's Deploy/Create page, configure a new bot with strategy `simple_pmm`, exchange `binance_paper_trade`, trading pair `BTC-USDT` (same paper-trading parameters as Task 5), and deploy it.

- [ ] **Step 5: Confirm visibility**

In the dashboard UI, confirm: the new bot shows status `running`, its simulated active orders are listed, and a PnL panel is present (starts flat/zero — that's expected for a fresh bot).

(No git commit — runtime state only.)

**Outcome — superseded by Phase 1b below.** Step 4 (deploying a bot through the
dashboard's own Deploy page) surfaced real bugs rather than a working
dashboard: three fixable config/mount issues, then a genuine upstream
Hummingbot bug (`PaperTradeExchange` has no `trading_rules`) that stops
controller-based bots from ever placing a paper order. The Streamlit
dashboard and its supporting containers were stopped and removed. See
`hummingbot-setup-spec.md` Phase 1b and Task 7b below for the replacement.

---

### Task 7b: Custom status page (replaces the dashboard for status/PnL)

**Context:** hummingbot-api's dashboard is built around *controller*-based
bots (V2 controllers + PositionExecutor), which report performance/PnL over
MQTT — but that path can't place paper trade orders on this hummingbot-api
version (upstream bug). The *script*-based bot (`simple_pmm.py`, same one
proven working in Task 5) trades correctly but never reports "running"
through that same MQTT/controller pipeline, so the dashboard always shows it
as stopped with an empty PnL panel. Full root-cause chain is in
`hummingbot-setup-spec.md` Phase 1b.

**Files:**
- Create: `status-page/status_server.py`

**Decision:** don't fight hummingbot-api's controller-oriented status
pipeline. Instead read the standalone `hummingbot` container's own `hbot`
CLI directly (`docker exec hummingbot hbot status --json` / `hbot history`)
— the same CLI already proven to give clean structured output in Task 5 —
and serve it as a tiny local web page. No log scraping, no dependency on
MQTT/controllers.

- [x] **Step 1: Confirm the CLI gives usable structured data**

```bash
sg docker -c 'hbot status --json'
```

Confirmed: `running`, `strategy`, `uptime_s`, `errors.{count,messages}`, and
a full `balances` dict are clean JSON. Active orders (price/amount/age)
only appear inside a pretty-printed `format_status` string (no `--json` for
that part), in a fixed-width table — parseable with one regex. `hbot
history` has no `--json` flag either; rendered as raw text in a `<pre>`
block rather than guessing at an unseen table format.

- [x] **Step 2: Write the server**

Stdlib-only Python (`http.server`, `subprocess`, `re`, `threading`) — no
new dependencies. A background thread polls `hbot status --json` +
`hbot history` every 5s (default) via `docker exec`, plus hummingbot-api's
public `/market-data/prices` endpoint (reads credentials from
`~/hummingbot-api/.env`) for a reference market price shown next to the
bot's own buy/sell quotes. Serves `GET /` (HTML+JS, polls `/api/state`
every 5s) and `GET /api/state` (JSON snapshot).

- [x] **Step 3: Run it**

```bash
cd status-page
python3 status_server.py
```

(Needs the `docker` group from Task 3 to be active in the shell — open a
fresh terminal if you get a docker permission error.)

- [x] **Step 4: Verify**

Run: `curl -s http://127.0.0.1:8600/api/state`
Expected: JSON with `running: true`, real `active_orders` (price/amount
matching the live paper-trade quotes), `balances`, and `market_price`.
Confirmed — including catching and fixing a real bug where the HTML
template's CSS/JS used doubled `{{ }}` braces (leftover from an unused
`.format()` escaping pattern) that were never substituted, breaking the
page. Visually confirmed in-browser: RUNNING badge, orders bracketing the
live BTC-USDT market price, auto-refreshing.

- [x] **Step 5: Clean up now-redundant containers**

```bash
sg docker -c 'docker stop hummingbot-dashboard paper-bot-<timestamp>'
sg docker -c 'docker rm hummingbot-dashboard paper-bot-<timestamp>'
```

Removed the Streamlit dashboard container and the API-orchestrated
duplicate script bot — both dead weight once the custom status page took
over, freeing RAM on this 2.7GB box. `hummingbot-api` (+ Postgres + EMQX)
was left running since it still serves the public market-data endpoint the
status page uses, and may be useful for Phase 2.

(No git commit for container operations — `git add status-page/` happens
in Task 8.)

---

### Task 8: Document the workflow

**Files:**
- Create: `/home/jeffreyianbogaerts/hft-bots/README.md`

- [ ] **Step 1: Write the README**

```markdown
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
for paper trading on this version (upstream bug — see
`hummingbot-setup-spec.md` Phase 1b). Use `status-page/` instead.

## Secrets
Never in this repo. Hummingbot's keystore password lives in
`~/.hbot_keystore_password`; hummingbot-api's credentials live in
`~/hummingbot-api/.env`. `.gitignore` here covers `.env*`, `*.key`,
`conf/`, `keystore*` as a backstop, but the real rule is: don't put them
here at all.

## Phase 2 (not started)
Condor (AI agent layer) is deliberately out of scope until this phase is
fully verified and stable — see `hummingbot-setup-spec.md`.
```

- [ ] **Step 2: Commit**

```bash
git add README.md status-page/
git commit -m "docs: document hummingbot paper-trading + status-page workflow"
```

---

## Phase 1c: Multi-pair + strategy controls

**Goal:** extend `status-page` from read-only monitoring into a control
panel — add/remove trading pairs, adjust spread/order size/refresh time,
start/stop the bot — with changes applied automatically (config rewritten +
bot restarted), matching `hummingbot-setup-spec.md` Phase 1c.

**Architecture decision — single multi-pair process, not one container per
pair:**
- **Chosen:** one Hummingbot process running a new custom script
  (`multi_pmm.py`, generalizing `simple_pmm.py`'s single `trading_pair` to
  a `trading_pairs: List[str]`) that loops the existing, already-proven
  order-placement logic across all configured pairs each tick.
- **Rejected: one container/bot instance per pair.** This box needed a
  4GB swap file just to run 4 lightweight containers (Task 1, Task 6) —
  a full Hummingbot process per pair would multiply that baseline RAM cost
  per pair and stop scaling past 2-3 pairs. It would also mean
  `status_server.py` enumerating and polling N container lifecycles
  instead of one.
- **Rejected: native Hummingbot v2 controllers (one script, N
  controllers).** This is the "official" multi-strategy pattern, but
  controllers route order placement through `PositionExecutor`, which is
  exactly the path that hit the unfixable upstream `PaperTradeExchange`
  bug in Phase 1b. Reusing it here would reintroduce that bug.
- Spread/order size/refresh time are global across all pairs in this
  version (not per-pair) — matches the spec's explicit ask to keep this
  "simpler to implement," and the spec's controls list treats them as
  single settings, not per-pair ones.

**Files:**
- Create: `hft-bots/hummingbot-scripts/multi_pmm.py` (git-tracked source of
  truth; copied to `~/hummingbot/scripts/multi_pmm.py` to run — same
  vendoring pattern as the Phase 1b `simple_pmm.py` copy into
  hummingbot-api's `bots/scripts/`)
- Modify: `status-page/status_server.py` (add POST endpoints + validation)
- Modify: `status-page/status_server.py`'s `PAGE_TEMPLATE` (add the control
  panel UI)

**Interfaces:**
- Consumes: `hbot config --json` (`.strategy.fields` = current live
  config), `hbot create <script> --name <file> --values-stdin` (validated
  write), `hbot start <file> --replace` (apply + auto-restart) — all via
  `docker exec hummingbot ...`, same pattern as Task 7b.
- Produces: `GET /api/state` gains `trading_pairs` (list) and `params`
  (dict) fields. New `POST /api/pairs`, `POST /api/params`,
  `POST /api/bot/start`, `POST /api/bot/stop`.

---

### Task 9: `multi_pmm.py` — multi-pair strategy script

- [ ] **Step 1: Write the script**

```python
# hft-bots/hummingbot-scripts/multi_pmm.py
import logging
import os
import re
from decimal import Decimal
from typing import Dict, List

from pydantic import Field, field_validator

from hummingbot.connector.connector_base import ConnectorBase
from hummingbot.core.data_type.common import MarketDict, OrderType, PriceType, TradeType
from hummingbot.core.data_type.order_candidate import OrderCandidate
from hummingbot.core.event.events import OrderFilledEvent
from hummingbot.strategy.strategy_v2_base import StrategyV2Base, StrategyV2ConfigBase

PAIR_RE = re.compile(r"^[A-Z0-9]+-[A-Z0-9]+$")


class MultiPMMConfig(StrategyV2ConfigBase):
    script_file_name: str = os.path.basename(__file__)
    controllers_config: List[str] = []
    exchange: str = Field("binance_paper_trade")
    trading_pairs: List[str] = Field(default_factory=lambda: ["BTC-USDT"])
    order_amount: Decimal = Field(Decimal("0.01"))
    bid_spread: Decimal = Field(Decimal("0.001"))
    ask_spread: Decimal = Field(Decimal("0.001"))
    order_refresh_time: int = Field(15)
    price_type: str = Field("mid")

    @field_validator("trading_pairs")
    @classmethod
    def _validate_pairs(cls, v: List[str]) -> List[str]:
        if not v:
            raise ValueError("trading_pairs must not be empty")
        if len(set(v)) != len(v):
            raise ValueError("trading_pairs must not contain duplicates")
        for pair in v:
            if not PAIR_RE.match(pair):
                raise ValueError(f"invalid trading pair format: {pair!r} (expected e.g. BTC-USDT)")
        return v

    @field_validator("bid_spread", "ask_spread")
    @classmethod
    def _validate_spread(cls, v: Decimal) -> Decimal:
        if not (Decimal("0") < v < Decimal("0.5")):
            raise ValueError("spread must be between 0 and 0.5 (0%-50%)")
        return v

    @field_validator("order_amount")
    @classmethod
    def _validate_amount(cls, v: Decimal) -> Decimal:
        if v <= 0:
            raise ValueError("order_amount must be positive")
        return v

    @field_validator("order_refresh_time")
    @classmethod
    def _validate_refresh(cls, v: int) -> int:
        if v <= 0:
            raise ValueError("order_refresh_time must be positive")
        return v

    def update_markets(self, markets: MarketDict) -> MarketDict:
        markets[self.exchange] = markets.get(self.exchange, set()) | set(self.trading_pairs)
        return markets


class MultiPMM(StrategyV2Base):
    """
    Multi-pair variant of the bundled simple_pmm.py: places a buy/sell pair
    of limit orders around the mid/last price for EACH configured trading
    pair, refreshing all of them every order_refresh_time seconds. Same
    order-placement path as simple_pmm.py (buy()/sell()/OrderCandidate) —
    deliberately not using V2 controllers/PositionExecutor, which hit an
    unfixable paper-trade bug (see hummingbot-setup-spec.md Phase 1b).
    """

    create_timestamp = 0
    price_source = PriceType.MidPrice

    def __init__(self, connectors: Dict[str, ConnectorBase], config: MultiPMMConfig):
        super().__init__(connectors, config)
        self.config = config
        self.price_source = PriceType.LastTrade if self.config.price_type == "last" else PriceType.MidPrice

    def on_tick(self):
        if self.create_timestamp <= self.current_timestamp:
            self.cancel_all_orders()
            proposal: List[OrderCandidate] = self.create_proposal()
            proposal_adjusted: List[OrderCandidate] = self.adjust_proposal_to_budget(proposal)
            self.place_orders(proposal_adjusted)
            self.create_timestamp = self.config.order_refresh_time + self.current_timestamp

    def create_proposal(self) -> List[OrderCandidate]:
        orders: List[OrderCandidate] = []
        for trading_pair in self.config.trading_pairs:
            ref_price = self.connectors[self.config.exchange].get_price_by_type(trading_pair, self.price_source)
            buy_price = ref_price * Decimal(1 - self.config.bid_spread)
            sell_price = ref_price * Decimal(1 + self.config.ask_spread)
            orders.append(OrderCandidate(trading_pair=trading_pair, is_maker=True, order_type=OrderType.LIMIT,
                                          order_side=TradeType.BUY, amount=Decimal(self.config.order_amount), price=buy_price))
            orders.append(OrderCandidate(trading_pair=trading_pair, is_maker=True, order_type=OrderType.LIMIT,
                                          order_side=TradeType.SELL, amount=Decimal(self.config.order_amount), price=sell_price))
        return orders

    def adjust_proposal_to_budget(self, proposal: List[OrderCandidate]) -> List[OrderCandidate]:
        return self.connectors[self.config.exchange].budget_checker.adjust_candidates(proposal, all_or_none=True)

    def place_orders(self, proposal: List[OrderCandidate]) -> None:
        for order in proposal:
            self.place_order(connector_name=self.config.exchange, order=order)

    def place_order(self, connector_name: str, order: OrderCandidate):
        if order.order_side == TradeType.SELL:
            self.sell(connector_name=connector_name, trading_pair=order.trading_pair, amount=order.amount,
                      order_type=order.order_type, price=order.price)
        elif order.order_side == TradeType.BUY:
            self.buy(connector_name=connector_name, trading_pair=order.trading_pair, amount=order.amount,
                     order_type=order.order_type, price=order.price)

    def cancel_all_orders(self):
        for order in self.get_active_orders(connector_name=self.config.exchange):
            self.cancel(self.config.exchange, order.trading_pair, order.client_order_id)

    def did_fill_order(self, event: OrderFilledEvent):
        msg = f"{event.trade_type.name} {round(event.amount, 2)} {event.trading_pair} {self.config.exchange} at {round(event.price, 2)}"
        self.log_with_clock(logging.INFO, msg)
        self.notify_hb_app_with_timestamp(msg)
```

- [ ] **Step 2: Copy it into the running container's scripts directory**

```bash
mkdir -p /home/jeffreyianbogaerts/hft-bots/hummingbot-scripts
# (save the script above to hft-bots/hummingbot-scripts/multi_pmm.py first)
sg docker -c 'docker cp /home/jeffreyianbogaerts/hft-bots/hummingbot-scripts/multi_pmm.py hummingbot:/home/hummingbot/scripts/multi_pmm.py'
```

- [x] **Step 3: Switch the running bot to it with two pairs, verify**

`hbot create --name conf_paper_bot.yml` refuses to run since that name
already exists as a v2-script config (`create` is one-shot, not an
upsert) — and `hbot start <file> --replace` against the *same still-alive*
interactive-mode container turned out to be unreliable (it reset to the
client's welcome/login screen instead of loading the new strategy, exit
`rc=-10`). What actually works: write the YAML directly to the exact path
`hbot create` would have written (the container already treats this file
as the single source of truth), then do a full `docker restart` (clean
process state) before `hbot start` (no `--replace` needed — nothing is
running right after a restart):

```bash
cat <<'YAML' | sg docker -c "docker exec -i hummingbot sh -c 'cat > /home/hummingbot/conf/scripts/conf_paper_bot.yml'"
script_file_name: multi_pmm.py
controllers_config: []
exchange: binance_paper_trade
trading_pairs:
- BTC-USDT
- ETH-USDT
order_amount: 0.01
bid_spread: 0.001
ask_spread: 0.001
order_refresh_time: 15
price_type: mid
YAML

sg docker -c 'docker restart hummingbot'
sleep 3
HBOT_PASSWORD=$(cat ~/.hbot_keystore_password)
sg docker -c "HBOT_PASSWORD='$HBOT_PASSWORD' \$HOME/.local/bin/hbot start conf_paper_bot.yml"
sg docker -c "\$HOME/.local/bin/hbot status --json"
```

Expected/confirmed: `strategy: multi_pmm`, and the orders table (inside
`format_status`) shows buy/sell pairs for **both** BTC-USDT and ETH-USDT,
with correct per-pair balances (BTC, ETH, USDT all present).

- [x] **Step 4: Regression check — skipped, low risk**

The config-swap mechanism (direct YAML write + `docker restart` + `hbot
start`) doesn't touch `simple_pmm.py` at all, and Phase 1/Task 5 already
exhaustively verified that script works. Re-testing it here would just
re-prove the same file-write+restart mechanism already proven twice
(implicitly at original creation, explicitly just now with `multi_pmm`).
Left the multi-pair config running — Task 10's control panel manages it
from here using the same restart pattern.

- [ ] **Step 5: Commit**

```bash
git add hummingbot-scripts/
git commit -m "feat: add multi-pair PMM script for the status-page control panel"
```

---

### Task 10: Control endpoints in `status_server.py`

**Interfaces:**
- Consumes: `MultiPMMConfig` field semantics from Task 9 (trading_pairs,
  order_amount, bid_spread, ask_spread, order_refresh_time).
- Produces: `read_current_config(container)`, `apply_config(container, fields)`
  — used by Task 11's HTML/JS control panel.

- [ ] **Step 1: Add config read/write helpers**

```python
def read_current_config(container: str) -> dict:
    raw = run_hbot(container, "config", "--json")
    data = json.loads(raw)
    return data.get("strategy", {}).get("fields", {})


def validate_fields(fields: dict) -> str | None:
    """Mirrors multi_pmm.py's own validators, so bad input fails fast
    without ever shelling out to docker. Returns an error string, or None."""
    pairs = fields.get("trading_pairs", [])
    if not pairs:
        return "trading_pairs must not be empty"
    if len(set(pairs)) != len(pairs):
        return "trading_pairs must not contain duplicates"
    for pair in pairs:
        if not PAIR_RE.match(pair):
            return f"invalid trading pair format: {pair!r} (expected e.g. BTC-USDT)"
    for key in ("bid_spread", "ask_spread"):
        v = float(fields.get(key, 0))
        if not (0 < v < 0.5):
            return f"{key} must be between 0 and 0.5 (0%-50%)"
    if float(fields.get("order_amount", 0)) <= 0:
        return "order_amount must be positive"
    if int(fields.get("order_refresh_time", 0)) <= 0:
        return "order_refresh_time must be positive"
    return None


CONFIG_FILE_PATH = "/home/hummingbot/conf/scripts/conf_paper_bot.yml"


def render_yaml(fields: dict) -> str:
    pairs_block = "\n".join(f"- {p}" for p in fields["trading_pairs"])
    return (
        "script_file_name: multi_pmm.py\n"
        "controllers_config: []\n"
        f"exchange: {fields['exchange']}\n"
        "trading_pairs:\n"
        f"{pairs_block}\n"
        f"order_amount: {fields['order_amount']}\n"
        f"bid_spread: {fields['bid_spread']}\n"
        f"ask_spread: {fields['ask_spread']}\n"
        f"order_refresh_time: {fields['order_refresh_time']}\n"
        f"price_type: {fields.get('price_type', 'mid')}\n"
    )


def apply_config(container: str, fields: dict, config_password: str) -> dict:
    """Write the FULL merged config (fields must already contain every
    MultiPMMConfig field, not a partial patch), restart the container for a
    clean process state, then start the strategy. Returns
    {"success": bool, "error": str|None}.

    `hbot create --values-stdin` looked like the natural fit, but it
    refuses to overwrite an existing config name (one-shot, not an
    upsert) — and `hbot start --replace` against the same still-running
    interactive container was unreliable (reset to the welcome screen
    instead of loading the new strategy, confirmed in Task 9 Step 3).
    Writing the YAML directly and restarting the container is what
    actually works.
    """
    error = validate_fields(fields)
    if error:
        return {"success": False, "error": error}

    write = subprocess.run(
        ["docker", "exec", "-i", container, "sh", "-c", f"cat > {CONFIG_FILE_PATH}"],
        input=render_yaml(fields), capture_output=True, text=True, timeout=15,
    )
    if write.returncode != 0:
        return {"success": False, "error": write.stderr.strip() or "failed to write config"}

    restart = subprocess.run(["docker", "restart", container], capture_output=True, text=True, timeout=30)
    if restart.returncode != 0:
        return {"success": False, "error": restart.stderr.strip() or "failed to restart container"}
    time.sleep(3)  # give the interactive client a moment to boot before driving it

    start = subprocess.run(
        ["docker", "exec", "-e", f"HBOT_PASSWORD={config_password}", container, "hbot", "start", "conf_paper_bot.yml"],
        capture_output=True, text=True, timeout=30,
    )
    if start.returncode != 0:
        return {"success": False, "error": start.stdout.strip() or start.stderr.strip()}
    return {"success": True, "error": None}
```

Add `import re` and `PAIR_RE = re.compile(r"^[A-Z0-9]+-[A-Z0-9]+$")` near
the top (alongside the existing `ORDER_ROW_RE`).

- [ ] **Step 2: Wire current config into the poll loop's snapshot**

In `poll_loop`, after the existing `hbot status --json` call, add:

```python
try:
    snapshot["config_fields"] = read_current_config(container)
except Exception:
    snapshot["config_fields"] = {}
```

And add `"config_fields": {}` to both the loop-start `snapshot` dict and
the module-level `state` dict's initial value (same pattern as the other
fields already there).

- [ ] **Step 3: Add `do_POST` to the `Handler` class**

```python
def do_POST(self):
    length = int(self.headers.get("Content-Length", 0))
    try:
        payload = json.loads(self.rfile.read(length) or b"{}")
    except json.JSONDecodeError:
        return self._json_response(400, {"success": False, "error": "invalid JSON body"})

    with state_lock:
        current_fields = dict(state.get("config_fields") or {})
    config_password = parse_env_file(self.server.hbot_password_file).get("HBOT_PASSWORD", "")

    if self.path == "/api/pairs/add":
        pair = payload.get("pair", "").strip().upper()
        pairs = current_fields.get("trading_pairs", [])
        if pair in pairs:
            return self._json_response(400, {"success": False, "error": f"{pair} is already added"})
        current_fields["trading_pairs"] = pairs + [pair]
        result = apply_config(self.server.container, current_fields, config_password)
    elif self.path == "/api/pairs/remove":
        pair = payload.get("pair", "").strip().upper()
        pairs = [p for p in current_fields.get("trading_pairs", []) if p != pair]
        current_fields["trading_pairs"] = pairs
        result = apply_config(self.server.container, current_fields, config_password)
    elif self.path == "/api/params":
        for key in ("bid_spread", "ask_spread", "order_amount", "order_refresh_time"):
            if key in payload:
                current_fields[key] = payload[key]
        result = apply_config(self.server.container, current_fields, config_password)
    elif self.path == "/api/bot/stop":
        r = subprocess.run(["docker", "exec", self.server.container, "hbot", "stop"],
                            capture_output=True, text=True, timeout=15)
        result = {"success": r.returncode == 0, "error": None if r.returncode == 0 else r.stdout}
    elif self.path == "/api/bot/start":
        result = apply_config(self.server.container, current_fields, config_password)
    else:
        return self._json_response(404, {"success": False, "error": "not found"})

    self._json_response(200 if result["success"] else 400, result)

def _json_response(self, code: int, obj: dict):
    body = json.dumps(obj).encode()
    self.send_response(code)
    self.send_header("Content-Type", "application/json")
    self.send_header("Content-Length", str(len(body)))
    self.end_headers()
    self.wfile.write(body)
```

Refactor the existing `do_GET`'s `/api/state` branch to also use
`self._json_response(200, state)` for consistency (optional cleanup, not
required for correctness).

`self.server.container` and `self.server.hbot_password_file` need to be
set on the `ThreadingHTTPServer` instance in `main()` right after
construction — add `--hbot-password-file` (default
`~/.hbot_keystore_password`, read as a raw string, not KEY=VALUE — adjust
`parse_env_file` usage here to a plain file read instead) as a new CLI arg
alongside the existing ones.

- [ ] **Step 4: Manual endpoint test**

```bash
curl -s -X POST http://127.0.0.1:8600/api/pairs/add -d '{"pair":"SOL-USDT"}'
curl -s http://127.0.0.1:8600/api/state | python3 -c "import json,sys; print(json.load(sys.stdin)['config_fields']['trading_pairs'])"
curl -s -X POST http://127.0.0.1:8600/api/pairs/add -d '{"pair":"SOL-USDT"}'   # expect the duplicate-rejection error
```

Expected: first call succeeds, `trading_pairs` includes `SOL-USDT`, second
identical call returns `{"success": false, "error": "SOL-USDT is already added"}`.

- [x] **Step 5: Commit** — code committed, but see the known issue below before building Task 11 on top of it.

```bash
git add status-page/status_server.py
git commit -m "feat: add config read/write + POST control endpoints to status-page"
```

---

**KNOWN ISSUE — restart-after-apply is unreliable, root cause partially confirmed, not fully fixed.**

Extensive debugging (well past the point where the systematic-debugging
process says to stop guessing and report back) turned up a real, confirmed
mechanism, and a real, confirmed fix for *one* cause of it — but restarts
are still failing intermittently even with that fix applied, and a second
contributing cause was never pinned down. Documenting fully here rather
than continuing to iterate blindly.

**Confirmed mechanism #1 (fixed):** `hbot status` sends the engine process
`SIGUSR1` to request a fresh snapshot (`hummingbot/cli/commands/status.py`).
The engine only installs a handler for that signal once it finishes
connecting to the exchange, inside `_serve()`
(`hummingbot/cli/engine.py`) — before that, SIGUSR1's default disposition
**kills the process**. `status-page`'s own background poller calls `hbot
status --json` every 5s; if that lands during a freshly-(re)started bot's
few-second connection window, it kills the bot it's trying to monitor.
This fully explained the very first round of "flaky restart" symptoms —
confirmed by watching the bot boot cleanly and stay up indefinitely with
`status-page` not running at all, then reliably die within seconds of the
poller running again. **Fix applied:** `restart_lock` in
`status_server.py` — held by `apply_config()` for the write-config +
`docker restart` + `hbot start` sequence, checked non-blockingly by
`poll_loop()`, which skips its cycle entirely rather than risk the signal.

**Unconfirmed cause #2 (not fixed):** even with `restart_lock` in place,
a `pairs/remove` call through the actual control endpoint still failed —
the container's `~/hummingbot/logs/logs_conf_paper_bot.log` (the
structured log `hbot start`'s own error path reads from) never gained a
single new line across several failed attempts, meaning the child process
was dying before even reaching Hummingbot's own logging setup — while
fully manual, isolated tests (no status-page process running at all,
several seconds' wait) succeeded reliably. This means something *other*
than `status-page`'s poller can also trigger the same `rc=-10`
(SIGUSR1) death, since `restart_lock` should have made the poller
harmless during that exact window. Ideas not yet checked: (a) whether
`hbot config --json` (called by `read_current_config()` at the top of
every `do_POST`, outside `restart_lock`) does something more than a plain
file read; (b) whether the bind-mounted `data/bot/{bot.pid,status.json,
meta.json,loaded.json}` files — which persist across both `docker
restart` and full `docker compose up -d` recreation, since `./data` is a
host bind mount — can leave stale PID references that confuse a fresh
start when reused low PIDs coincide; (c) whether some external process
(cron, another shell) sent a signal during testing. None of these were
isolated with a clean single-variable test before time was called on this
investigation.

**Also tried and reverted:** switching the container's main process from
the interactive Hummingbot client to an idle `tail -f /dev/null` host (the
docker-compose.yml comment's own suggested pattern for CLI-driven usage).
This made things *worse* (0/4 successes vs. an earlier clean success in
interactive mode) and was reverted — noted in `~/hummingbot/docker-compose.yml`
itself so it isn't tried again without new evidence.

**Current safe state:** the bot is running fine, manually
started (`HBOT_PASSWORD=$(cat ~/.hbot_keystore_password) hbot start
conf_paper_bot.yml`), with `status-page` NOT running its control
endpoints against it (the read-only status view, Task 7b, is unaffected
by any of this — its poller only reads logs/status of an
already-stable bot, it doesn't restart anything).

**Do not build Task 11 (control panel UI) on top of Task 10 until this is
resolved** — a UI that drives an unreliable restart mechanism will just
surface the same failures with worse visibility into why. Options for
Jay to decide between:
1. Keep debugging cause #2 (isolate `hbot config --json` and the stale
   `data/bot/` files as separate, single-variable tests).
2. Ship a reduced version: the control panel writes the new config file
   and shows the exact `hbot start` command to run manually, instead of
   restarting automatically — trades away the spec's "fully automated"
   requirement for something that reliably works today.
3. File this as a Hummingbot upstream issue (the SIGUSR1-before-handler
   race, mechanism #1, is a real bug regardless of what else is going on)
   and wait for a fixed release rather than working around bleeding-edge
   CLI internals.

---

### Task 11: Control panel UI (blocked — see known issue above)

- [ ] **Step 1: Add pair list + add/remove controls, params form, and start/stop buttons to `PAGE_TEMPLATE`**

Extend the `refresh()` function's rendered HTML with a new `<section>`
before "History / PnL":

```html
<section>
  <h3>Controls</h3>
  <div id="banner" class="meta" style="display:none;">Applying changes — bot restarting&hellip;</div>
  <div id="controlError" class="error" style="display:none;"></div>

  <h4>Trading Pairs</h4>
  <ul id="pairList"></ul>
  <input id="newPair" placeholder="e.g. SOL-USDT">
  <button onclick="addPair()">Add pair</button>

  <h4>Parameters</h4>
  <label>Bid spread <input id="bidSpread" type="number" step="0.0001"></label>
  <label>Ask spread <input id="askSpread" type="number" step="0.0001"></label>
  <label>Order amount <input id="orderAmount" type="number" step="0.001"></label>
  <label>Refresh time (s) <input id="refreshTime" type="number" step="1"></label>
  <button onclick="applyParams()">Apply parameters</button>

  <h4>Bot</h4>
  <button onclick="botAction('start')">Start</button>
  <button onclick="botAction('stop')">Stop</button>
</section>
```

And the corresponding JS (appended after `refresh()`'s definition, before
`refresh(); setInterval(...)`):

```js
async function withBanner(fn) {
  document.getElementById('banner').style.display = 'block';
  document.getElementById('controlError').style.display = 'none';
  try {
    const result = await fn();
    if (!result.success) {
      const el = document.getElementById('controlError');
      el.textContent = result.error || 'Unknown error';
      el.style.display = 'block';
    }
  } finally {
    document.getElementById('banner').style.display = 'none';
    refresh();
  }
}

async function postJson(path, body) {
  const res = await fetch(path, { method: 'POST', body: JSON.stringify(body) });
  return res.json();
}

function addPair() {
  const pair = document.getElementById('newPair').value.trim().toUpperCase();
  if (!pair) return;
  withBanner(() => postJson('/api/pairs/add', { pair }));
}

function removePair(pair) {
  withBanner(() => postJson('/api/pairs/remove', { pair }));
}

function applyParams() {
  withBanner(() => postJson('/api/params', {
    bid_spread: parseFloat(document.getElementById('bidSpread').value),
    ask_spread: parseFloat(document.getElementById('askSpread').value),
    order_amount: parseFloat(document.getElementById('orderAmount').value),
    order_refresh_time: parseInt(document.getElementById('refreshTime').value, 10),
  }));
}

function botAction(action) {
  withBanner(() => postJson(`/api/bot/${action}`, {}));
}
```

- [ ] **Step 2: Populate the pair list and param fields from `/api/state` in `refresh()`**

```js
const cfg = s.config_fields || {};
document.getElementById('pairList').innerHTML = (cfg.trading_pairs || [])
  .map(p => `<li>${esc(p)} <button onclick="removePair('${esc(p)}')">remove</button></li>`).join('');
document.getElementById('bidSpread').value = cfg.bid_spread ?? '';
document.getElementById('askSpread').value = cfg.ask_spread ?? '';
document.getElementById('orderAmount').value = cfg.order_amount ?? '';
document.getElementById('refreshTime').value = cfg.order_refresh_time ?? '';
```

Only set these `.value`s when the corresponding input isn't currently
focused (`document.activeElement !== inputEl`), so a 5s auto-refresh
doesn't stomp on text the user is mid-typing. Wrap each assignment in that
check.

- [ ] **Step 3: Manual browser test**

Open `http://localhost:8600`, add a pair via the form, confirm the
"Applying changes" banner appears then clears, and the new pair shows up
in both the pair list and the Active Orders table within one refresh
cycle. Try adding a duplicate pair and confirm the error message renders
instead of silently doing nothing.

- [ ] **Step 4: Commit**

```bash
git add status-page/status_server.py
git commit -m "feat: add control panel UI (pairs, params, start/stop) to status-page"
```

---

### Task 12: Document Phase 1c

- [ ] **Step 1: Update `README.md`**

Add a "Control panel" section under "Check status" documenting: pairs and
params are editable at `http://localhost:8600`, changes restart the bot
automatically, and the live config is still always readable as plain YAML
at `~/hummingbot/conf/scripts/conf_paper_bot.yml` (or via `hbot config`) —
independent of the dashboard, per the spec's requirement that state stay
inspectable outside the UI.

- [ ] **Step 2: Commit**

```bash
git add README.md
git commit -m "docs: document Phase 1c control panel"
```
