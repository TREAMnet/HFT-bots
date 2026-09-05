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
