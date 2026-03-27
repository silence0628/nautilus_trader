#!/usr/bin/env python3

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[1]
REPO_ROOT = Path(__file__).resolve().parents[4]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from adapters.binance_testnet import REQUIRED_TESTNET_ENV_VARS
from adapters.binance_testnet import build_spot_testnet_runtime
from configs import build_research_context
from configs import load_env_file


def main() -> None:
    env_path = ROOT_DIR / "configs" / "testnet.env"
    loaded = load_env_file(env_path)
    context = build_research_context("testnet")

    node, _, _ = build_spot_testnet_runtime("testnet")
    try:
        payload = {
            "status": "ready",
            "profile": "testnet",
            "loaded_env_file": str(env_path) if env_path.exists() else None,
            "loaded_env_keys": sorted(loaded.keys()),
            "required_env_keys": list(REQUIRED_TESTNET_ENV_VARS),
            "venue": context["venue"],
            "pairs": context["trading"]["pairs"],
            "timeframe": context["timeframe"],
            "bar_types": [asset.bar_type for asset in context["parameters"].assets],
            "instrument_ids": [asset.instrument_id for asset in context["parameters"].assets],
            "trader_id": context["runtime"].get("trader_id"),
            "testnet": context["runtime"].get("testnet"),
        }
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    finally:
        node.dispose()


if __name__ == "__main__":
    main()
