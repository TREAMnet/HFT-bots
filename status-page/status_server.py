#!/usr/bin/env python3
"""
Lightweight local status page for the paper-trading Hummingbot instance.

Why this exists: hummingbot-api's own dashboard status/PnL panel only works
for controller-based bots, and controller-based paper trading currently hits
an upstream bug (PaperTradeExchange has no attribute 'trading_rules') that
stops it from ever placing an order. The script-based bot (simple_pmm.py)
trades correctly but never reports "running" through that pipeline. See
hummingbot-setup-spec.md, Phase 1b, for the full writeup.

Source of truth here is the `hbot` CLI against the standalone `hummingbot`
container from Phase 1 step 1/2 (`docker exec hummingbot hbot ...`) — the
same one already proven to trade correctly in Phase 1 step 2. No log
scraping, no dependency on hummingbot-api's MQTT/controller pipeline.

Phase 1c adds a control panel: add/remove trading pairs, edit strategy
params, start/stop the bot. Applying a pair/param change writes and
validates the config file but does NOT restart the bot automatically —
it shows the exact command to run instead. Automatic restart was tried
and dropped after proving intermittently unreliable (a real signal race
between this page's own poller and the engine's startup sequence, fixed;
plus a second, unconfirmed cause that wasn't) — see
hummingbot-setup-spec.md Phase 1c for the full writeup.

Usage: python3 status_server.py [--container hummingbot] [--port 8600]
No third-party dependencies — stdlib only.
"""
import argparse
import base64
import json
import os
import re
import subprocess
import threading
import time
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

ORDER_ROW_RE = re.compile(
    r"^\s*(\S+)\s+(\S+)\s+(buy|sell)\s+([\d.]+)\s+([\d.]+)\s+(\S+)\s*$",
    re.IGNORECASE,
)
PAIR_RE = re.compile(r"^[A-Z0-9]+-[A-Z0-9]+$")
CONFIG_FILE_PATH = "/home/hummingbot/conf/scripts/conf_paper_bot.yml"

state_lock = threading.Lock()
# `hbot status` sends SIGUSR1 to ask the engine for a fresh snapshot — but the
# engine only installs a SIGUSR1 handler once it finishes connecting to the
# exchange (hummingbot/cli/engine.py `_serve()`); before that, SIGUSR1's
# default disposition kills the process outright.
#
# An in-process lock around this page's OWN `hbot start` calls was tried
# first and wasn't enough: the whole point of the semi-automated design
# (hummingbot-setup-spec.md Phase 1c) is that the USER runs the restart
# command in their own terminal while this page is open — proven by
# direct A/B test to reliably fail while the poller is running and
# reliably succeed the instant it's stopped. A lock this process controls
# can't protect a command run outside this process. Instead, BOOT_GRACE_S
# below makes the poller check the bot's process age (a plain file read of
# meta.json — no signal involved) before every `hbot status` call, and
# skip it entirely while the bot is still inside its connection window,
# regardless of who started it.
BOOT_GRACE_S = 12.0
state = {
    "fetched_at": None,
    "running": False,
    "strategy": None,
    "uptime_s": None,
    "error_count": 0,
    "errors": [],
    "balances": {},
    "active_orders": [],
    "history_text": "",
    "fetch_error": None,
    "market_prices": {},
    "config_fields": {},
}


def parse_env_file(path: str) -> dict:
    """Minimal KEY=VALUE parser for a hummingbot-api .env file."""
    values = {}
    try:
        with open(os.path.expanduser(path)) as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, _, val = line.partition("=")
                values[key.strip()] = val.strip()
    except FileNotFoundError:
        pass
    return values


def fetch_market_prices(api_url: str, user: str, password: str, connector: str, pairs: list, timeout: int = 8) -> dict:
    """Public market prices via hummingbot-api — independent of any bot's own
    state. One request for every pair currently configured, so the price
    list on the page tracks whatever pairs are actually active."""
    if not user or not password or not pairs:
        return {}
    body = json.dumps({"connector_name": connector, "trading_pairs": pairs}).encode()
    req = urllib.request.Request(
        f"{api_url}/market-data/prices", data=body, method="POST",
        headers={
            "Content-Type": "application/json",
            "Authorization": "Basic " + base64.b64encode(f"{user}:{password}".encode()).decode(),
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = json.loads(resp.read())
            return data.get("prices", {})
    except (urllib.error.URLError, TimeoutError, ValueError, OSError):
        return {}


def read_secret_file(path: str) -> str:
    """Reads a raw single-value secret file (not KEY=VALUE), e.g. the
    Hummingbot keystore password written by `hbot start`'s setup step."""
    try:
        with open(os.path.expanduser(path)) as f:
            return f.read().strip()
    except FileNotFoundError:
        return ""


def run_hbot(container: str, *args: str, timeout: int = 15) -> str:
    result = subprocess.run(
        ["docker", "exec", container, "hbot", *args],
        capture_output=True, text=True, timeout=timeout,
    )
    return result.stdout


CONFIG_NUMERIC_FIELDS = {
    "order_amount": float, "bid_spread": float, "ask_spread": float,
    "order_refresh_time": int,
}


def read_current_config(container: str) -> dict:
    """Read the live script config straight off disk (`docker exec ... cat`)
    instead of `hbot config --json`. The latter pays hbot CLI's own cold
    Python-interpreter-startup cost on every single invocation (~4-5s,
    measured, vs. ~0.2s for a plain `cat`) — since this is called on every
    poll AND at the top of every control-panel POST (to merge a partial
    change into the full config before writing), that made every control
    action take 5+ seconds with no progress feedback, which is what made
    "add pair" etc. look like a no-op (hummingbot-setup-spec.md Phase 1c).
    This is a minimal decoder for exactly the format render_yaml() writes
    below, not a general YAML parser — safe because this file is only ever
    written by that function."""
    result = subprocess.run(
        ["docker", "exec", container, "cat", CONFIG_FILE_PATH],
        capture_output=True, text=True, timeout=10,
    )
    if result.returncode != 0:
        return {}
    fields: dict = {}
    pairs: list = []
    in_pairs = False
    for line in result.stdout.splitlines():
        if in_pairs and line.startswith("- "):
            pairs.append(line[2:].strip())
            continue
        in_pairs = False
        if line.strip() == "trading_pairs:":
            in_pairs = True
            continue
        key, sep, value = line.partition(":")
        if not sep:
            continue
        key, value = key.strip(), value.strip()
        if key == "controllers_config":
            continue  # always [] for our script deploys
        cast = CONFIG_NUMERIC_FIELDS.get(key)
        fields[key] = cast(value) if cast else value
    fields["trading_pairs"] = pairs
    return fields


def bot_boot_age_s(container: str) -> float:
    """Seconds since the engine process (any bot, started by us or manually
    in the user's own terminal) began booting — a plain `cat` of meta.json,
    never a signal. Returns +inf if unreadable/unparseable (nothing running,
    or genuinely old enough that timing doesn't matter)."""
    result = subprocess.run(
        ["docker", "exec", container, "cat", "/home/hummingbot/data/bot/meta.json"],
        capture_output=True, text=True, timeout=10,
    )
    if result.returncode != 0:
        return float("inf")
    try:
        started_at = json.loads(result.stdout)["started_at"]
        return time.time() - float(started_at)
    except (json.JSONDecodeError, KeyError, TypeError, ValueError):
        return float("inf")


def validate_fields(fields: dict) -> str:
    """Mirrors multi_pmm.py's own pydantic validators, so bad input fails
    fast without ever shelling out to docker. Returns an error string, or
    an empty string when the fields are valid."""
    pairs = fields.get("trading_pairs", [])
    if not pairs:
        return "trading_pairs must not be empty"
    if len(set(pairs)) != len(pairs):
        return "trading_pairs must not contain duplicates"
    for pair in pairs:
        if not PAIR_RE.match(pair):
            return f"invalid trading pair format: {pair!r} (expected e.g. BTC-USDT)"
    for key in ("bid_spread", "ask_spread"):
        try:
            v = float(fields.get(key, 0))
        except (TypeError, ValueError):
            return f"{key} must be a number"
        if not (0 < v < 0.5):
            return f"{key} must be between 0 and 0.5 (0%-50%)"
    try:
        if float(fields.get("order_amount", 0)) <= 0:
            return "order_amount must be positive"
    except (TypeError, ValueError):
        return "order_amount must be a number"
    try:
        if int(fields.get("order_refresh_time", 0)) <= 0:
            return "order_refresh_time must be positive"
    except (TypeError, ValueError):
        return "order_refresh_time must be a whole number"
    return ""


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


MANUAL_RESTART_COMMAND = 'hbot stop; HBOT_PASSWORD=$(cat ~/.hbot_keystore_password) hbot start conf_paper_bot.yml'


def write_config(container: str, fields: dict) -> dict:
    """Validate and write the FULL merged config (fields must already contain
    every MultiPMMConfig field, not a partial patch) to disk. Does NOT
    restart or start the bot — see hummingbot-setup-spec.md Phase 1c: full
    automation (write + restart + verify) was attempted and found
    intermittently unreliable (a confirmed signal race between this page's
    own poller and the engine's startup sequence, fixed, plus a second,
    unconfirmed cause that wasn't). The decision was to keep the reliable
    part (validated config writing) and drop automatic restart in favor of
    a displayed manual command. Returns
    {"success": bool, "error": str, "manual_command": str}.
    """
    error = validate_fields(fields)
    if error:
        return {"success": False, "error": error, "manual_command": ""}

    write = subprocess.run(
        ["docker", "exec", "-i", container, "sh", "-c", f"cat > {CONFIG_FILE_PATH}"],
        input=render_yaml(fields), capture_output=True, text=True, timeout=15,
    )
    if write.returncode != 0:
        return {"success": False, "error": write.stderr.strip() or "failed to write config", "manual_command": ""}
    return {"success": True, "error": "", "manual_command": MANUAL_RESTART_COMMAND}


def parse_active_orders(format_status: str):
    orders = []
    in_orders_section = False
    for line in format_status.splitlines():
        if line.strip().startswith("Orders:"):
            in_orders_section = True
            continue
        if not in_orders_section:
            continue
        if not line.strip():
            continue
        if line.strip().startswith("Exchange"):
            continue  # header row
        m = ORDER_ROW_RE.match(line)
        if m:
            exchange, market, side, price, amount, age = m.groups()
            orders.append({
                "exchange": exchange, "market": market, "side": side,
                "price": float(price), "amount": float(amount), "age": age,
            })
    return orders


def poll_loop(container: str, interval: float, api_url: str, api_env: str, price_connector: str, default_pair: str):
    while True:
        snapshot = _poll_once(container, api_url, api_env, price_connector, default_pair)
        with state_lock:
            state.update(snapshot)
        time.sleep(interval)


def _poll_once(container: str, api_url: str, api_env: str, price_connector: str, default_pair: str) -> dict:
    snapshot = {
        "fetched_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "running": False, "strategy": None, "uptime_s": None,
        "error_count": 0, "errors": [], "balances": {},
        "active_orders": [], "history_text": "", "fetch_error": None,
        "market_prices": {},
        "config_fields": {},
    }

    # Never send `hbot status`'s SIGUSR1 while the engine is still inside its
    # exchange-connection window (see BOOT_GRACE_S above) — that includes a
    # restart the user just ran manually in their own terminal, which this
    # process has no other way to know about.
    if bot_boot_age_s(container) < BOOT_GRACE_S:
        snapshot["fetch_error"] = "bot is starting up — status check paused briefly to avoid interrupting it"
        try:
            snapshot["config_fields"] = read_current_config(container)
        except Exception:
            pass
        return snapshot

    try:
        status_raw = run_hbot(container, "status", "--json")
        status = json.loads(status_raw)
        snapshot["running"] = status.get("running", False)
        snapshot["strategy"] = status.get("strategy")
        snapshot["uptime_s"] = status.get("uptime_s")
        errors = status.get("errors", {})
        snapshot["error_count"] = errors.get("count", 0)
        snapshot["errors"] = errors.get("messages", [])
        snapshot["balances"] = status.get("balances") or {}
        snapshot["active_orders"] = parse_active_orders(status.get("format_status") or "")
    except Exception as exc:
        snapshot["fetch_error"] = f"status: {exc}"

    try:
        snapshot["config_fields"] = read_current_config(container)
    except Exception:
        snapshot["config_fields"] = {}

    try:
        snapshot["history_text"] = run_hbot(container, "history").strip()
    except Exception as exc:
        snapshot["history_text"] = ""
        snapshot["fetch_error"] = (snapshot["fetch_error"] or "") + f" | history: {exc}"

    pairs = snapshot["config_fields"].get("trading_pairs") or [default_pair]
    env = parse_env_file(api_env)
    snapshot["market_prices"] = fetch_market_prices(
        api_url, env.get("USERNAME"), env.get("PASSWORD"), price_connector, pairs
    )
    return snapshot


PAGE_TEMPLATE = """<!doctype html>
<html>
<head>
<meta charset="utf-8">
<title>Hummingbot Paper Trading Status</title>
<style>
  body { font-family: -apple-system, sans-serif; background: #0f1115; color: #e6e6e6; margin: 0; padding: 24px; }
  h1 { font-size: 1.3rem; margin-bottom: 4px; }
  h4 { margin: 16px 0 6px; font-size: 0.95rem; color: #ccc; }
  .meta { color: #888; font-size: 0.85rem; margin-bottom: 20px; }
  .badge { display: inline-block; padding: 2px 10px; border-radius: 10px; font-size: 0.8rem; font-weight: 600; }
  .badge.running { background: #1f7a3f; color: #fff; }
  .badge.stopped { background: #7a1f1f; color: #fff; }
  .price-badge { display: inline-block; padding: 2px 10px; margin: 2px 4px 2px 0; border-radius: 10px; font-size: 0.8rem; background: #1a1d24; border: 1px solid #2a2d34; }
  section { margin-bottom: 28px; }
  table { border-collapse: collapse; width: 100%; max-width: 640px; }
  th, td { text-align: left; padding: 6px 12px; border-bottom: 1px solid #2a2d34; font-size: 0.9rem; }
  th { color: #999; font-weight: 500; }
  pre { background: #1a1d24; padding: 12px; border-radius: 6px; overflow-x: auto; font-size: 0.85rem; }
  .error { color: #ff6b6b; }
  .banner { background: #2a2410; border: 1px solid #6b5a1a; color: #e8d27a; padding: 8px 12px; border-radius: 6px; margin-bottom: 10px; }
  ul#pairList { list-style: none; padding: 0; margin: 0 0 8px; }
  ul#pairList li { padding: 4px 0; }
  ul#pairList button, .controls button { margin-left: 8px; cursor: pointer; }
  .controls input[type=text], .controls input[type=number] { width: 110px; background: #1a1d24; border: 1px solid #2a2d34; color: #e6e6e6; padding: 4px 6px; border-radius: 4px; }
  .controls label { display: inline-block; margin: 4px 14px 4px 0; }
  .controls button { background: #2a2d34; border: 1px solid #3a3d44; color: #e6e6e6; padding: 5px 12px; border-radius: 4px; }
  .controls button:hover { background: #33363d; }
</style>
</head>
<body>
<h1>Hummingbot Paper Trading Status</h1>
<div class="meta">Auto-refreshes every 5s. Source: <code>docker exec hummingbot hbot status/history</code>.</div>
<div id="app">Loading&hellip;</div>

<!--
Feedback elements for control actions live outside #app on purpose: #app's
innerHTML is fully replaced by every refresh() call (each control action
triggers one immediately, and the 5s poll triggers one on its own), which
was wiping out the just-set banner/error/manual-command text before it was
ever visible — the "no-op" bug from hummingbot-setup-spec.md Phase 1c.
Keeping them outside the regenerated block means refresh() can't touch them.
-->
<div id="banner" class="banner" style="display:none;"></div>
<div id="controlError" class="error" style="display:none;"></div>
<div id="manualCommand" style="display:none;">
  <div class="meta">Config written. Run this in your own terminal to apply it (see hummingbot-setup-spec.md Phase 1c for why this step is manual):</div>
  <pre id="manualCommandText"></pre>
</div>

<!--
The Controls section lives outside #app for the same reason as the feedback
elements above, and it's not optional here the way it was for those: every
refresh() (each poll tick, not just control actions) was destroying and
recreating this section's inputs wholesale via #app's innerHTML replace,
which silently wiped whatever the user was mid-typing into newPair/bidSpread/
askSpread/orderAmount/refreshTime — the setIfIdle() focus guard below looks
right but can never work against a node that gets replaced out from under it
every 5s, since the freshly-created node was never the focused one. Keeping
these inputs outside #app means the SAME node persists across refreshes, so
setIfIdle's focus check is actually checking something real. See
hummingbot-setup-spec.md Phase 1c (screen-recording follow-up) for the report.
-->
<section class="controls">
  <h3>Controls</h3>

  <h4>Trading Pairs</h4>
  <ul id="pairList"></ul>
  <input id="newPair" type="text" placeholder="e.g. SOL-USDT">
  <button onclick="addPair()">Add pair</button>

  <h4>Parameters</h4>
  <label>Bid spread <input id="bidSpread" type="number" step="0.0001"></label>
  <label>Ask spread <input id="askSpread" type="number" step="0.0001"></label>
  <label>Order amount <input id="orderAmount" type="number" step="0.001"></label>
  <label>Refresh time (s) <input id="refreshTime" type="number" step="1"></label>
  <div><button onclick="applyParams()">Write parameters</button></div>

  <h4>Bot</h4>
  <button onclick="botAction('start')">Start</button>
  <button onclick="botAction('stop')">Stop</button>
</section>

<script>
function esc(s) { return (s ?? "").toString().replace(/[&<>]/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;'}[c])); }

async function refresh() {
  const res = await fetch('/api/state');
  const s = await res.json();
  const badge = s.running
    ? '<span class="badge running">RUNNING</span>'
    : '<span class="badge stopped">STOPPED</span>';
  const uptime = s.uptime_s != null ? Math.round(s.uptime_s) + 's' : '-';

  let ordersRows = s.active_orders.map(o =>
    `<tr><td>${esc(o.market)}</td><td>${esc(o.side)}</td><td>${o.price}</td><td>${o.amount}</td><td>${esc(o.age)}</td></tr>`
  ).join('') || '<tr><td colspan="5">No active orders</td></tr>';

  let balanceRows = Object.entries(s.balances).flatMap(([exch, assets]) =>
    Object.entries(assets).map(([asset, amt]) =>
      `<tr><td>${esc(exch)}</td><td>${esc(asset)}</td><td>${amt}</td></tr>`)
  ).join('') || '<tr><td colspan="3">No balance data</td></tr>';

  let errorsHtml = s.errors.length
    ? `<pre class="error">${s.errors.map(esc).join('\\n')}</pre>` : '<div>None</div>';

  // One badge per currently-active pair — appears/disappears as pairs are
  // added/removed, rather than a single hardcoded pair.
  const priceEntries = Object.entries(s.market_prices || {});
  const priceBadges = priceEntries.length
    ? priceEntries.map(([pair, price]) => `<span class="price-badge">${esc(pair)}: <b>${price}</b></span>`).join('')
    : '<span class="meta">No price data</span>';

  document.getElementById('app').innerHTML = `
    <div>${badge} &nbsp; strategy: <b>${esc(s.strategy || '-')}</b> &nbsp; uptime: ${uptime} &nbsp; errors (10m): ${s.error_count}</div>
    <div class="meta">Last fetched: ${esc(s.fetched_at)}${s.fetch_error ? ' — <span class="error">' + esc(s.fetch_error) + '</span>' : ''}</div>

    <section>
      <h3>Active Orders</h3>
      <div style="margin-bottom:8px">${priceBadges}</div>
      <table><thead><tr><th>Market</th><th>Side</th><th>Price</th><th>Amount</th><th>Age</th></tr></thead>
      <tbody>${ordersRows}</tbody></table>
    </section>

    <section>
      <h3>Balances (paper)</h3>
      <table><thead><tr><th>Exchange</th><th>Asset</th><th>Total</th></tr></thead>
      <tbody>${balanceRows}</tbody></table>
    </section>

    <section>
      <h3>History / PnL</h3>
      <pre>${esc(s.history_text) || 'No trades found.'}</pre>
    </section>

    <section>
      <h3>Recent Errors</h3>
      ${errorsHtml}
    </section>
  `;

  // Populate the pair list and param fields from live config — skip an
  // input the user currently has focused so a 5s auto-refresh doesn't
  // stomp on text they're mid-typing.
  const cfg = s.config_fields || {};
  document.getElementById('pairList').innerHTML = (cfg.trading_pairs || [])
    .map(p => `<li>${esc(p)} <button onclick="removePair('${esc(p)}')">remove</button></li>`).join('')
    || '<li class="meta">No pairs configured</li>';

  const setIfIdle = (id, value) => {
    const el = document.getElementById(id);
    if (document.activeElement !== el) el.value = value ?? '';
  };
  setIfIdle('bidSpread', cfg.bid_spread);
  setIfIdle('askSpread', cfg.ask_spread);
  setIfIdle('orderAmount', cfg.order_amount);
  setIfIdle('refreshTime', cfg.order_refresh_time);
}

async function withBanner(message, fn) {
  const banner = document.getElementById('banner');
  const errEl = document.getElementById('controlError');
  const cmdEl = document.getElementById('manualCommand');
  banner.textContent = message;
  banner.style.display = 'block';
  errEl.style.display = 'none';
  cmdEl.style.display = 'none';
  try {
    const result = await fn();
    if (!result.success) {
      errEl.textContent = result.error || 'Unknown error';
      errEl.style.display = 'block';
    } else if (result.manual_command) {
      document.getElementById('manualCommandText').textContent = result.manual_command;
      cmdEl.style.display = 'block';
    }
  } finally {
    banner.style.display = 'none';
    refresh();
  }
}

async function postJson(path, body) {
  const res = await fetch(path, { method: 'POST', body: JSON.stringify(body) });
  return res.json();
}

function addPair() {
  const input = document.getElementById('newPair');
  const pair = input.value.trim().toUpperCase();
  if (!pair) return;
  withBanner('Writing config…', () => postJson('/api/pairs/add', { pair })).then(() => input.value = '');
}

function removePair(pair) {
  withBanner('Writing config…', () => postJson('/api/pairs/remove', { pair }));
}

function applyParams() {
  withBanner('Writing config…', () => postJson('/api/params', {
    bid_spread: parseFloat(document.getElementById('bidSpread').value),
    ask_spread: parseFloat(document.getElementById('askSpread').value),
    order_amount: parseFloat(document.getElementById('orderAmount').value),
    order_refresh_time: parseInt(document.getElementById('refreshTime').value, 10),
  }));
}

function botAction(action) {
  withBanner(action === 'start' ? 'Starting…' : 'Stopping…', () => postJson(`/api/bot/${action}`, {}));
}

refresh();
setInterval(refresh, 5000);
</script>
</body>
</html>
"""


class Handler(BaseHTTPRequestHandler):
    def log_message(self, format, *args):
        pass  # keep stdout quiet; state fetch already prints nothing

    def _json_response(self, code: int, obj: dict):
        body = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path == "/api/state":
            with state_lock:
                body = json.dumps(state).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        elif self.path == "/" or self.path == "/index.html":
            body = PAGE_TEMPLATE.encode()
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        else:
            self.send_response(404)
            self.end_headers()

    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0))
        try:
            payload = json.loads(self.rfile.read(length) or b"{}")
        except json.JSONDecodeError:
            return self._json_response(400, {"success": False, "error": "invalid JSON body"})

        # Read the live config fresh rather than trusting the cached poll
        # snapshot — that cache can be briefly empty/stale right after the
        # server starts (or after an out-of-band change), and acting on a
        # stale copy silently corrupts fields the caller isn't touching.
        try:
            current_fields = read_current_config(self.server.container)
        except Exception:
            current_fields = {}
        config_password = read_secret_file(self.server.hbot_password_file)
        container = self.server.container

        if self.path == "/api/pairs/add":
            pair = payload.get("pair", "").strip().upper()
            pairs = current_fields.get("trading_pairs", [])
            if pair in pairs:
                return self._json_response(400, {"success": False, "error": f"{pair} is already added"})
            current_fields["trading_pairs"] = pairs + [pair]
            result = write_config(container, current_fields)
        elif self.path == "/api/pairs/remove":
            pair = payload.get("pair", "").strip().upper()
            current_fields["trading_pairs"] = [p for p in current_fields.get("trading_pairs", []) if p != pair]
            result = write_config(container, current_fields)
        elif self.path == "/api/params":
            for key in ("bid_spread", "ask_spread", "order_amount", "order_refresh_time"):
                if key in payload:
                    current_fields[key] = payload[key]
            result = write_config(container, current_fields)
        elif self.path == "/api/bot/stop":
            r = subprocess.run(["docker", "exec", container, "hbot", "stop"],
                                capture_output=True, text=True, timeout=15)
            result = {"success": r.returncode == 0, "error": "" if r.returncode == 0 else r.stdout.strip()}
        elif self.path == "/api/bot/start":
            # Not a config-writing action, so it's fine to launch directly —
            # only the write+auto-restart combo was dropped (see
            # write_config's docstring). No lock needed here: the poller's
            # own BOOT_GRACE_S check (bot_boot_age_s) already keeps it from
            # sending `hbot status`'s signal into this call's boot window,
            # the same as it would for a manual restart.
            r = subprocess.run(
                ["docker", "exec", "-e", f"HBOT_PASSWORD={config_password}", container, "hbot", "start", "conf_paper_bot.yml"],
                capture_output=True, text=True, timeout=30,
            )
            result = {"success": r.returncode == 0, "error": "" if r.returncode == 0 else (r.stdout.strip() or r.stderr.strip())}
        else:
            return self._json_response(404, {"success": False, "error": "not found"})

        self._json_response(200 if result["success"] else 400, result)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--container", default="hummingbot", help="Docker container name running the hbot CLI target")
    parser.add_argument("--port", type=int, default=8600)
    parser.add_argument("--interval", type=float, default=5.0, help="Poll interval in seconds")
    parser.add_argument("--api-url", default="http://127.0.0.1:8000", help="hummingbot-api base URL (public market-data only)")
    parser.add_argument("--api-env", default="~/hummingbot-api/.env", help="Path to hummingbot-api's .env, for its USERNAME/PASSWORD")
    parser.add_argument("--price-connector", default="binance", help="Connector to read the public reference price from")
    parser.add_argument("--price-pair", default="BTC-USDT", help="Trading pair to show the market price for")
    parser.add_argument("--hbot-password-file", default="~/.hbot_keystore_password",
                         help="Path to the raw Hummingbot keystore password, for restart-after-apply")
    args = parser.parse_args()

    poller = threading.Thread(
        target=poll_loop,
        args=(args.container, args.interval, args.api_url, args.api_env, args.price_connector, args.price_pair),
        daemon=True,
    )
    poller.start()

    server = ThreadingHTTPServer(("127.0.0.1", args.port), Handler)
    server.container = args.container
    server.hbot_password_file = args.hbot_password_file
    print(f"Status page: http://localhost:{args.port}  (polling container '{args.container}' every {args.interval}s)")
    server.serve_forever()


if __name__ == "__main__":
    main()
