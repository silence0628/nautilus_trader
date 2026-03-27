#!/usr/bin/env python3

from __future__ import annotations

import json
import threading
import time
import sys
import urllib.parse
import urllib.request
from decimal import Decimal
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[1]
REPO_ROOT = Path(__file__).resolve().parents[4]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from adapters.binance_testnet import build_spot_testnet_runtime
from configs import load_env_file


def _fetch_latest_prices(pairs: list[str], testnet: bool = True) -> dict[str, float]:
    base_url = "https://testnet.binance.vision" if testnet else "https://api.binance.com"
    prices: dict[str, float] = {}
    for pair in pairs:
        symbol = pair.replace("-", "")
        url = (
            f"{base_url}/api/v3/ticker/price?"
            + urllib.parse.urlencode({"symbol": symbol})
        )
        with urllib.request.urlopen(url, timeout=5) as response:
            payload = json.loads(response.read().decode("utf-8"))
        prices[pair] = float(payload["price"])
    return prices


def _format_prices(prices: dict[str, float]) -> str:
    parts = []
    for pair, price in prices.items():
        decimals = 2 if pair.startswith("BTC") else 4
        parts.append(f"{pair}={price:.{decimals}f}")
    return " | ".join(parts)


def _heartbeat_loop(strategy, pairs: list[str], interval_secs: int = 60) -> None:
    while True:
        try:
            summary = strategy.get_runtime_summary()
            prices = _fetch_latest_prices(pairs)
            latest_bar_time = summary.get("latest_bar_time_utc") or "waiting-first-1h-bar"
            portfolio_value = Decimal(str(summary.get("portfolio_value", 0.0))).quantize(Decimal("0.01"))
            max_dd = Decimal(str(summary.get("max_observed_drawdown_pct", 0.0))).quantize(Decimal("0.01"))
            plan_items = summary.get("plan_items", [])
            plan_text = f" | plan_items={len(plan_items)}" if plan_items else ""
            print(
                f"[HEARTBEAT {time.strftime('%Y-%m-%d %H:%M:%S')}] "
                f"{_format_prices(prices)} | latest_bar={latest_bar_time} | "
                f"portfolio={portfolio_value} | max_dd={max_dd}% | "
                f"rebalances={summary.get('total_rebalances', 0)} | "
                f"kill_switch={summary.get('kill_switch_triggered', False)}"
                f"{plan_text}",
                flush=True,
            )
        except Exception as exc:  # pragma: no cover - heartbeat should never crash runner
            print(f"[HEARTBEAT-ERROR] {exc}", flush=True)
        time.sleep(interval_secs)


def main() -> None:
    load_env_file(ROOT_DIR / "configs" / "testnet.env")
    node, strategy, context = build_spot_testnet_runtime("testnet")
    pairs = list(context["trading"]["pairs"])
    heartbeat_thread = threading.Thread(
        target=_heartbeat_loop,
        args=(strategy, pairs, 60),
        daemon=True,
    )
    heartbeat_thread.start()
    try:
        node.run()
    finally:
        node.dispose()


if __name__ == "__main__":
    main()
