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
# default disposition kills the process outright. This lock keeps the
# background poller's `hbot status` calls from ever overlapping a restart's
# vulnerable boot window — held by apply_config() for the full
# restart-then-start sequence, checked non-blockingly by poll_loop().
restart_lock = threading.Lock()
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
    "market_price": None,
    "market_pair": None,
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


def fetch_market_price(api_url: str, user: str, password: str, connector: str, pair: str, timeout: int = 8):
    """Public market price via hummingbot-api — independent of any bot's own state."""
    if not user or not password:
        return None
    body = json.dumps({"connector_name": connector, "trading_pairs": [pair]}).encode()
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
            return data.get("prices", {}).get(pair)
    except (urllib.error.URLError, TimeoutError, ValueError, OSError):
        return None


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


def read_current_config(container: str) -> dict:
    raw = run_hbot(container, "config", "--json")
    data = json.loads(raw)
    return data.get("strategy", {}).get("fields", {})


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


def apply_config(container: str, fields: dict, config_password: str) -> dict:
    """Write the FULL merged config (fields must already contain every
    MultiPMMConfig field, not a partial patch), restart the container for a
    clean process state, then start the strategy. Returns
    {"success": bool, "error": str}.

    `hbot create --values-stdin` looked like the natural fit, but it
    refuses to overwrite an existing config name (one-shot, not an
    upsert) — and `hbot start --replace` against the same still-running
    interactive container was unreliable (reset to the welcome screen
    instead of loading the new strategy). Writing the YAML directly and
    restarting the container is what actually works — see the setup plan.
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

    # Hold restart_lock for the whole restart+start sequence so the background
    # poller (poll_loop) can't send a `hbot status` SIGUSR1 into the engine's
    # vulnerable pre-handler boot window and kill it (see the restart_lock
    # comment above — this was the actual root cause of every "flaky restart"
    # symptom seen while building this feature, not a timing issue). `hbot
    # start`'s own exit code is already the correct readiness signal: it
    # internally waits for the engine's initial status.json write (a plain
    # file read, not a signal) before returning, so no extra polling here.
    with restart_lock:
        restart = subprocess.run(["docker", "restart", container], capture_output=True, text=True, timeout=30)
        if restart.returncode != 0:
            return {"success": False, "error": restart.stderr.strip() or "failed to restart container"}

        start = subprocess.run(
            ["docker", "exec", "-e", f"HBOT_PASSWORD={config_password}", container, "hbot", "start", "conf_paper_bot.yml"],
            capture_output=True, text=True, timeout=30,
        )
    if start.returncode != 0:
        return {"success": False, "error": start.stdout.strip() or start.stderr.strip()}
    return {"success": True, "error": ""}


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


def poll_loop(container: str, interval: float, api_url: str, api_env: str, price_connector: str, price_pair: str):
    while True:
        # `hbot status` sends the engine a signal that's fatal during its boot
        # window (see the restart_lock comment near its definition) — never
        # run it while apply_config() is mid-restart. Skip this cycle
        # entirely rather than partially update state with a mix of fresh
        # and stale fields.
        if not restart_lock.acquire(blocking=False):
            time.sleep(interval)
            continue
        try:
            snapshot = _poll_once(container, api_url, api_env, price_connector, price_pair)
        finally:
            restart_lock.release()

        with state_lock:
            state.update(snapshot)

        time.sleep(interval)


def _poll_once(container: str, api_url: str, api_env: str, price_connector: str, price_pair: str) -> dict:
    snapshot = {
        "fetched_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "running": False, "strategy": None, "uptime_s": None,
        "error_count": 0, "errors": [], "balances": {},
        "active_orders": [], "history_text": "", "fetch_error": None,
        "market_price": None, "market_pair": price_pair,
        "config_fields": {},
    }
    try:
        status_raw = run_hbot(container, "status", "--json")
        status = json.loads(status_raw)
        snapshot["running"] = status.get("running", False)
        snapshot["strategy"] = status.get("strategy")
        snapshot["uptime_s"] = status.get("uptime_s")
        errors = status.get("errors", {})
        snapshot["error_count"] = errors.get("count", 0)
        snapshot["errors"] = errors.get("messages", [])
        snapshot["balances"] = status.get("balances", {})
        snapshot["active_orders"] = parse_active_orders(status.get("format_status", ""))
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

    env = parse_env_file(api_env)
    snapshot["market_price"] = fetch_market_price(
        api_url, env.get("USERNAME"), env.get("PASSWORD"), price_connector, price_pair
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
  .meta { color: #888; font-size: 0.85rem; margin-bottom: 20px; }
  .badge { display: inline-block; padding: 2px 10px; border-radius: 10px; font-size: 0.8rem; font-weight: 600; }
  .badge.running { background: #1f7a3f; color: #fff; }
  .badge.stopped { background: #7a1f1f; color: #fff; }
  section { margin-bottom: 28px; }
  table { border-collapse: collapse; width: 100%; max-width: 640px; }
  th, td { text-align: left; padding: 6px 12px; border-bottom: 1px solid #2a2d34; font-size: 0.9rem; }
  th { color: #999; font-weight: 500; }
  pre { background: #1a1d24; padding: 12px; border-radius: 6px; overflow-x: auto; font-size: 0.85rem; }
  .error { color: #ff6b6b; }
</style>
</head>
<body>
<h1>Hummingbot Paper Trading Status</h1>
<div class="meta">Auto-refreshes every 5s. Source: <code>docker exec hummingbot hbot status/history</code>.</div>
<div id="app">Loading&hellip;</div>
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

  const marketPrice = s.market_price != null
    ? `${esc(s.market_pair)}: <b>${s.market_price}</b>`
    : `${esc(s.market_pair)}: <span class="error">unavailable</span>`;

  document.getElementById('app').innerHTML = `
    <div>${badge} &nbsp; strategy: <b>${esc(s.strategy || '-')}</b> &nbsp; uptime: ${uptime} &nbsp; errors (10m): ${s.error_count} &nbsp; market ${marketPrice}</div>
    <div class="meta">Last fetched: ${esc(s.fetched_at)}${s.fetch_error ? ' — <span class="error">' + esc(s.fetch_error) + '</span>' : ''}</div>

    <section>
      <h3>Active Orders</h3>
      <div class="meta">Reference market price — ${marketPrice}</div>
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
            result = apply_config(container, current_fields, config_password)
        elif self.path == "/api/pairs/remove":
            pair = payload.get("pair", "").strip().upper()
            current_fields["trading_pairs"] = [p for p in current_fields.get("trading_pairs", []) if p != pair]
            result = apply_config(container, current_fields, config_password)
        elif self.path == "/api/params":
            for key in ("bid_spread", "ask_spread", "order_amount", "order_refresh_time"):
                if key in payload:
                    current_fields[key] = payload[key]
            result = apply_config(container, current_fields, config_password)
        elif self.path == "/api/bot/stop":
            r = subprocess.run(["docker", "exec", container, "hbot", "stop"],
                                capture_output=True, text=True, timeout=15)
            result = {"success": r.returncode == 0, "error": "" if r.returncode == 0 else r.stdout.strip()}
        elif self.path == "/api/bot/start":
            result = apply_config(container, current_fields, config_password)
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
