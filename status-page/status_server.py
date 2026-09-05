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

state_lock = threading.Lock()
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


def run_hbot(container: str, *args: str, timeout: int = 15) -> str:
    result = subprocess.run(
        ["docker", "exec", container, "hbot", *args],
        capture_output=True, text=True, timeout=timeout,
    )
    return result.stdout


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
        snapshot = {
            "fetched_at": time.strftime("%Y-%m-%d %H:%M:%S"),
            "running": False, "strategy": None, "uptime_s": None,
            "error_count": 0, "errors": [], "balances": {},
            "active_orders": [], "history_text": "", "fetch_error": None,
            "market_price": None, "market_pair": price_pair,
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
            snapshot["history_text"] = run_hbot(container, "history").strip()
        except Exception as exc:
            snapshot["history_text"] = ""
            snapshot["fetch_error"] = (snapshot["fetch_error"] or "") + f" | history: {exc}"

        env = parse_env_file(api_env)
        snapshot["market_price"] = fetch_market_price(
            api_url, env.get("USERNAME"), env.get("PASSWORD"), price_connector, price_pair
        )

        with state_lock:
            state.update(snapshot)

        time.sleep(interval)


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


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--container", default="hummingbot", help="Docker container name running the hbot CLI target")
    parser.add_argument("--port", type=int, default=8600)
    parser.add_argument("--interval", type=float, default=5.0, help="Poll interval in seconds")
    parser.add_argument("--api-url", default="http://127.0.0.1:8000", help="hummingbot-api base URL (public market-data only)")
    parser.add_argument("--api-env", default="~/hummingbot-api/.env", help="Path to hummingbot-api's .env, for its USERNAME/PASSWORD")
    parser.add_argument("--price-connector", default="binance", help="Connector to read the public reference price from")
    parser.add_argument("--price-pair", default="BTC-USDT", help="Trading pair to show the market price for")
    args = parser.parse_args()

    poller = threading.Thread(
        target=poll_loop,
        args=(args.container, args.interval, args.api_url, args.api_env, args.price_connector, args.price_pair),
        daemon=True,
    )
    poller.start()

    server = ThreadingHTTPServer(("127.0.0.1", args.port), Handler)
    print(f"Status page: http://localhost:{args.port}  (polling container '{args.container}' every {args.interval}s)")
    server.serve_forever()


if __name__ == "__main__":
    main()
