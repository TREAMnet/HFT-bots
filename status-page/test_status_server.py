#!/usr/bin/env python3
"""Unit tests for status_server.py's pure helpers.

Stdlib only (unittest) — same "no third-party dependencies" rule as the
server itself. Run: python3 -m unittest discover -s status-page
"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import status_server as ss  # noqa: E402


SAMPLE_CONF_CLIENT = """\
instance_id: abc123
mqtt_bridge:
  mqtt_host: localhost
paper_trade:
  paper_trade_exchanges:
  - binance
  - kucoin
  paper_trade_account_balance:
    BTC: 1.0
    USDT: 100000.0
    ETH: 20.0
    DOGE: 1000000.0
color:
  top_pane: '#000000'
"""


class ExtractPaperBalances(unittest.TestCase):
    def test_returns_the_single_connector_submap(self):
        balances = {"binance_paper_trade": {"BTC": 1.0, "USDT": 99950.0}}
        self.assertEqual(
            ss.extract_paper_balances(balances),
            {"BTC": 1.0, "USDT": 99950.0},
        )

    def test_empty_when_no_connector_present(self):
        self.assertEqual(ss.extract_paper_balances({}), {})

    def test_empty_when_connector_submap_is_empty(self):
        self.assertEqual(ss.extract_paper_balances({"binance_paper_trade": {}}), {})


class ParsePaperBalanceBlock(unittest.TestCase):
    def test_reads_the_asset_to_amount_map(self):
        self.assertEqual(
            ss.parse_paper_balance_block(SAMPLE_CONF_CLIENT),
            {"BTC": 1.0, "USDT": 100000.0, "ETH": 20.0, "DOGE": 1000000.0},
        )

    def test_empty_when_section_absent(self):
        self.assertEqual(ss.parse_paper_balance_block("instance_id: abc\ncolor:\n  x: y\n"), {})

    def test_stops_at_the_next_dedented_key(self):
        # 'color:' must not be swallowed into the balance map.
        self.assertNotIn("color", ss.parse_paper_balance_block(SAMPLE_CONF_CLIENT))


class MergeBalances(unittest.TestCase):
    def test_overlays_snapshot_values_onto_current(self):
        current = {"BTC": 1.0, "USDT": 100000.0}
        snapshot = {"BTC": 0.97, "USDT": 99950.0}
        self.assertEqual(ss.merge_balances(current, snapshot), {"BTC": 0.97, "USDT": 99950.0})

    def test_keeps_current_assets_missing_from_snapshot(self):
        current = {"BTC": 1.0, "USDT": 100000.0, "HBOT": 10000000.0}
        snapshot = {"BTC": 0.97}
        merged = ss.merge_balances(current, snapshot)
        self.assertEqual(merged["USDT"], 100000.0)
        self.assertEqual(merged["HBOT"], 10000000.0)
        self.assertEqual(merged["BTC"], 0.97)

    def test_adds_snapshot_assets_missing_from_current(self):
        self.assertEqual(ss.merge_balances({}, {"BTC": 0.5}), {"BTC": 0.5})


class RenderBalanceJson(unittest.TestCase):
    def test_is_valid_json_with_sorted_keys(self):
        import json
        out = ss.render_balance_json({"USDT": 1.0, "BTC": 2.0})
        self.assertEqual(json.loads(out), {"USDT": 1.0, "BTC": 2.0})
        self.assertLess(out.index('"BTC"'), out.index('"USDT"'))

    def test_rounds_long_floats(self):
        import json
        out = ss.render_balance_json({"USDT": 99949.80163396867})
        self.assertEqual(json.loads(out)["USDT"], 99949.80163397)


class ShouldCheckpoint(unittest.TestCase):
    def test_true_when_running_with_balances_and_interval_elapsed(self):
        self.assertTrue(ss.should_checkpoint(
            running=True, balances={"BTC": 1.0}, last_ts=0.0, now=61.0, interval=60.0))

    def test_false_when_not_running(self):
        self.assertFalse(ss.should_checkpoint(
            running=False, balances={"BTC": 1.0}, last_ts=0.0, now=61.0, interval=60.0))

    def test_false_when_balances_empty(self):
        self.assertFalse(ss.should_checkpoint(
            running=True, balances={}, last_ts=0.0, now=61.0, interval=60.0))

    def test_false_when_interval_not_elapsed(self):
        self.assertFalse(ss.should_checkpoint(
            running=True, balances={"BTC": 1.0}, last_ts=30.0, now=61.0, interval=60.0))


class CheckpointPaperBalances(unittest.TestCase):
    def test_merges_snapshot_onto_disk_config_then_writes(self):
        captured = {}

        def fake_read(container):
            return {"BTC": 1.0, "USDT": 100000.0, "HBOT": 10000000.0}

        def fake_write(container, balances):
            captured["balances"] = balances
            return {"success": True, "error": ""}

        result = ss.checkpoint_paper_balances(
            "hummingbot", {"BTC": 0.97, "USDT": 99950.0},
            _read=fake_read, _write=fake_write,
        )
        self.assertTrue(result["success"])
        self.assertEqual(result["assets"], 3)
        self.assertEqual(
            captured["balances"],
            {"BTC": 0.97, "USDT": 99950.0, "HBOT": 10000000.0},
        )

    def test_reports_write_failure(self):
        result = ss.checkpoint_paper_balances(
            "hummingbot", {"BTC": 0.97},
            _read=lambda c: {"BTC": 1.0},
            _write=lambda c, b: {"success": False, "error": "boom"},
        )
        self.assertFalse(result["success"])
        self.assertEqual(result["error"], "boom")

    def test_skips_write_when_snapshot_is_empty(self):
        called = {"write": False}

        def fake_write(container, balances):
            called["write"] = True
            return {"success": True, "error": ""}

        result = ss.checkpoint_paper_balances(
            "hummingbot", {}, _read=lambda c: {"BTC": 1.0}, _write=fake_write,
        )
        self.assertFalse(result["success"])
        self.assertFalse(called["write"])
        self.assertIn("no balance", result["error"].lower())


if __name__ == "__main__":
    unittest.main()
