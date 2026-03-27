#!/usr/bin/env python3

from __future__ import annotations

import json
import threading
import time
import sys
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[1]
REPO_ROOT = Path(__file__).resolve().parents[4]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from adapters.binance_testnet import build_spot_testnet_runtime
from configs import load_env_file


def _heartbeat_loop(strategy, interval_secs: int = 60) -> None:
    while True:
        try:
            summary = strategy.get_runtime_summary()
            compact = {
                "latest_bar_time_utc": summary.get("latest_bar_time_utc"),
                "portfolio_value": round(summary.get("portfolio_value", 0.0), 2),
                "peak_portfolio_value": round(summary.get("peak_portfolio_value", 0.0), 2),
                "max_observed_drawdown_pct": round(summary.get("max_observed_drawdown_pct", 0.0), 2),
                "kill_switch_triggered": summary.get("kill_switch_triggered"),
                "total_rebalances": summary.get("total_rebalances"),
                "daily_rebalance_count": summary.get("daily_rebalance_count"),
                "volatility_pause_count": summary.get("volatility_pause_count"),
                "trend_pause_count": summary.get("trend_pause_count"),
                "order_denied_count": summary.get("order_denied_count"),
                "order_rejected_count": summary.get("order_rejected_count"),
                "plan_items": summary.get("plan_items", []),
            }
            print(
                f"[HEARTBEAT {time.strftime('%Y-%m-%d %H:%M:%S')}] "
                f"{json.dumps(compact, ensure_ascii=False)}",
                flush=True,
            )
        except Exception as exc:  # pragma: no cover - heartbeat should never crash runner
            print(f"[HEARTBEAT-ERROR] {exc}", flush=True)
        time.sleep(interval_secs)


def main() -> None:
    load_env_file(ROOT_DIR / "configs" / "testnet.env")
    node, strategy, _ = build_spot_testnet_runtime("testnet")
    heartbeat_thread = threading.Thread(
        target=_heartbeat_loop,
        args=(strategy, 60),
        daemon=True,
    )
    heartbeat_thread.start()
    try:
        node.run()
    finally:
        node.dispose()


if __name__ == "__main__":
    main()
