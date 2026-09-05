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

- [ ] **Step 1: Initialize the repo**

Run: `git init` (from `/home/jeffreyianbogaerts/hft-bots`)
Expected: `Initialized empty Git repository in .../hft-bots/.git/`

- [ ] **Step 2: Write `.gitignore`**

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

- [ ] **Step 3: Verify status**

Run: `git status`
Expected: `.gitignore` and `hummingbot-setup-spec.md` listed as untracked (new) files; no ignored paths shown. `CLAUDE.md` is deliberately left out of this task — see the note in the final summary; do not commit it here.

- [ ] **Step 4: Commit**

```bash
git add .gitignore hummingbot-setup-spec.md
git commit -m "chore: initialize repo with .gitignore before first commit"
```

---

### Task 3: Install Docker Engine + Compose

**Files:** none (host packages only)

- [ ] **Step 1: Update apt and install Docker from Debian's own repos**

Debian trixie is very new — use the in-distro packages rather than Docker's own apt repo, which may not yet publish a `trixie` channel:

```bash
sudo apt-get update
sudo apt-get install -y docker.io docker-compose-v2 docker-buildx uidmap
```

- [ ] **Step 2: Enable and start the daemon**

```bash
sudo systemctl enable --now docker
```

Expected: `systemctl status docker` shows `active (running)`.

- [ ] **Step 3: Let your user run docker without sudo**

```bash
sudo usermod -aG docker "$USER"
```

Group membership needs a new login session to take effect. For the rest of this plan, either open a fresh shell/`newgrp docker`, or prefix commands with `sudo` if `docker ...` reports a permission error.

- [ ] **Step 4: Verify Docker works end-to-end (daemon + network pull)**

Run: `docker run --rm hello-world` (or `sudo docker run --rm hello-world` if group membership hasn't applied yet)
Expected: output includes `Hello from Docker!`

(No git commit — no repo file changed.)

---

### Task 4: Clone and build Hummingbot, verify the `hbot` CLI

**Files:** none in `hft-bots` — Hummingbot clones to `~/hummingbot`, outside this repo per the spec.

- [ ] **Step 1: Clone**

```bash
git clone https://github.com/hummingbot/hummingbot.git ~/hummingbot
```

- [ ] **Step 2: Docker setup**

```bash
cd ~/hummingbot
make setup
```

When prompted about including **Gateway** (DEX middleware), answer **`n`** — we're only paper-trading against a centralized exchange (`binance_paper_trade`), Gateway isn't needed, and skipping it saves RAM on this box.

- [ ] **Step 3: Deploy the container**

```bash
make deploy
```

- [ ] **Step 4: Link the `hbot` CLI onto the host PATH**

```bash
make link-cli
```

- [ ] **Step 5: Verify**

Run: `hbot --help`
Expected: prints Hummingbot's CLI usage/help text (confirms the wrapper reaches the running container).

(No git commit — Hummingbot's codebase never enters `hft-bots`.)

---

### Task 5: Verify paper trading (simple_pmm on binance_paper_trade)

**Files:** none in `hft-bots`.

**Interfaces:**
- Consumes: working `hbot` CLI from Task 4.

- [ ] **Step 1: Create the strategy config**

```bash
hbot create simple_pmm --name conf_paper_bot.yml \
     --set exchange=binance_paper_trade --set trading_pair=BTC-USDT
```

If the wizard prompts further for strategy parameters not covered by `--set`, use these concrete values: `bid_spread=0.5`, `ask_spread=0.5`, `order_refresh_time=60`, `order_amount=0.01`. Accept the default price source (current market price).

- [ ] **Step 2: Start it**

```bash
hbot start conf_paper_bot.yml
```

- [ ] **Step 3: Confirm simulated activity**

```bash
hbot status
```

Expected: status output shows the strategy running, simulated balances for the paper-trade account, and active buy/sell orders around the current BTC-USDT market price.

- [ ] **Step 4: Stop it to free RAM before the next tasks**

Inside the `hbot` session: `stop`, then exit the CLI (`exit`).
Expected: `hbot status` (run again) reports the strategy is no longer active.

(No git commit — this is a runtime verification, not a repo change.)

---

### Task 6: Deploy the hummingbot-api backend

**Files:** none in `hft-bots` — clones to `~/hummingbot-api`. Its `.env` (created by `make setup`) holds real secrets and must never be copied into this repo.

- [ ] **Step 1: Clone**

```bash
git clone https://github.com/hummingbot/hummingbot-api.git ~/hummingbot-api
cd ~/hummingbot-api
```

- [ ] **Step 2: Run setup**

```bash
make setup
```

Answer the interactive prompts:
- API username: `admin`
- API password: choose one, write it down somewhere outside this repo — you'll need it in Task 7
- Config password (encrypts bot credentials): choose one, write it down the same way
- Tailscale: answer **`n`** — not needed for local-only personal use

- [ ] **Step 3: Deploy**

```bash
make deploy
```

- [ ] **Step 4: Verify all three containers are up**

Run: `docker ps --format '{{.Names}}\t{{.Status}}'`
Expected: three lines for `hummingbot-api`, `hummingbot-broker`, `hummingbot-postgres`, all showing `Up`.

- [ ] **Step 5: Verify the API answers**

Run: `curl -s -o /dev/null -w '%{http_code}\n' http://127.0.0.1:8000/docs`
Expected: `200`

(No git commit — secrets live in `~/hummingbot-api/.env`, outside this repo.)

---

### Task 7: Deploy the Streamlit dashboard, hand-wired to hummingbot-api

**Files:** none in `hft-bots` (runtime container only).

**Interfaces:**
- Consumes: hummingbot-api listening on `127.0.0.1:8000` from Task 6, and the API username/password chosen in Task 6 Step 2.

- [ ] **Step 1: Run the dashboard container with host networking**

The dashboard's own default `docker-compose.yml` expects a service named `backend-api` on a shared Compose network, which doesn't exist here since hummingbot-api was deployed as its own separate stack. Use `--network host` instead so the dashboard container can reach the API at `127.0.0.1:8000` directly:

```bash
docker run -d --name hummingbot-dashboard \
  --network host \
  --restart unless-stopped \
  -e AUTH_SYSTEM_ENABLED=False \
  -e BACKEND_API_HOST=localhost \
  -e BACKEND_API_PORT=8000 \
  -e BACKEND_API_USERNAME=admin \
  -e BACKEND_API_PASSWORD=<the password you set in Task 6 Step 2> \
  hummingbot/dashboard:latest
```

- [ ] **Step 2: Verify it's serving**

Run: `curl -s -o /dev/null -w '%{http_code}\n' http://127.0.0.1:8501`
Expected: `200`

- [ ] **Step 3: Open it in the browser**

Open `http://localhost:8501` in the Chromebook's Chrome browser (Crostini forwards container ports to ChromeOS automatically — same as any other `localhost` port from this container).
Expected: the Hummingbot Dashboard UI loads without an auth prompt (since `AUTH_SYSTEM_ENABLED=False`).

- [ ] **Step 4: Deploy a bot through the dashboard (this is what makes it visible)**

hummingbot-api orchestrates bots itself via its mounted Docker socket — it doesn't automatically pick up the standalone bot from Task 5. In the dashboard's Deploy/Create page, configure a new bot with strategy `simple_pmm`, exchange `binance_paper_trade`, trading pair `BTC-USDT` (same paper-trading parameters as Task 5), and deploy it.

- [ ] **Step 5: Confirm visibility**

In the dashboard UI, confirm: the new bot shows status `running`, its simulated active orders are listed, and a PnL panel is present (starts flat/zero — that's expected for a fresh bot).

(No git commit — runtime state only.)

---

### Task 8: Document the workflow

**Files:**
- Create: `/home/jeffreyianbogaerts/hft-bots/README.md`

- [ ] **Step 1: Write the README**

```markdown
# HFT-bots — Hummingbot Paper-Trading Setup

Personal Hummingbot setup notes. This repo holds configs/notes only —
Hummingbot itself lives in separate clones outside this repo (see below).

## Layout
- `~/hummingbot` — Hummingbot client (Docker), driven via the `hbot` CLI
- `~/hummingbot-api` — backend API + Postgres + EMQX broker (Docker Compose)
- `hummingbot-dashboard` container — Streamlit UI, hand-wired to the API

## Start everything
```bash
cd ~/hummingbot && make deploy          # Hummingbot client
cd ~/hummingbot-api && make deploy      # API + Postgres + EMQX
docker start hummingbot-dashboard       # Dashboard (created once via `docker run`, see setup plan)
```

## Stop everything
```bash
docker stop hummingbot-dashboard
cd ~/hummingbot-api && make stop
cd ~/hummingbot && docker compose down
```

## Check status
- CLI bot: `hbot status` (after `hbot start <config>.yml`)
- API: `curl -s http://127.0.0.1:8000/docs` should return the Swagger page
- Dashboard: open `http://localhost:8501` in the browser

## Create a new paper-trading bot via the CLI
```bash
hbot create simple_pmm --name conf_paper_bot.yml \
     --set exchange=binance_paper_trade --set trading_pair=BTC-USDT
hbot start conf_paper_bot.yml
```

## Secrets
Never in this repo. Live in `~/hummingbot-api/.env` and Hummingbot's own
encrypted keystore. `.gitignore` here covers `.env*`, `*.key`, `conf/`,
`keystore*` as a backstop, but the real rule is: don't put them here at all.

## Phase 2 (not started)
Condor (AI agent layer) is deliberately out of scope until this phase is
fully verified and stable — see `hummingbot-setup-spec.md`.
```

- [ ] **Step 2: Commit**

```bash
git add README.md
git commit -m "docs: document hummingbot paper-trading + dashboard workflow"
```
