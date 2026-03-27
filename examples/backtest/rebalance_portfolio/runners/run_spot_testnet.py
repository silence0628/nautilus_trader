#!/usr/bin/env python3

from __future__ import annotations

import sys
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[1]
REPO_ROOT = Path(__file__).resolve().parents[4]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from adapters.binance_testnet import build_spot_testnet_node
from configs import load_env_file


def main() -> None:
    load_env_file(ROOT_DIR / "configs" / "testnet.env")
    node = build_spot_testnet_node("testnet")
    try:
        node.run()
    finally:
        node.dispose()


if __name__ == "__main__":
    main()
