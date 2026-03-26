#!/usr/bin/env python3

from __future__ import annotations

import csv
import json
import subprocess
import textwrap
from pathlib import Path


RESULTS_DIR = Path(__file__).resolve().parent / "results"
RESULTS_DIR.mkdir(parents=True, exist_ok=True)
JSON_PATH = RESULTS_DIR / "rebalance_speculum_adapter_parameter_sweep.json"
CSV_PATH = RESULTS_DIR / "rebalance_speculum_adapter_parameter_sweep.csv"
MD_PATH = RESULTS_DIR / "rebalance_speculum_adapter_parameter_sweep.md"


def _build_remote_script() -> str:
    return textwrap.dedent(
        """
        import asyncio
        import json
        from copy import deepcopy
        from decimal import Decimal
        from uuid import UUID

        from sqlalchemy import select

        from app.db.base import async_session_factory
        from app.db.models.backtest import Backtest
        from app.db.models.strategy import Strategy
        from app.engine.adapters.nautilus.adapter import NautilusAdapter
        from app.engine.core.base import BacktestRequest
        from app.engine.data_loader import DataLoader
        from app.utils.config_extractor import extract_trading_config

        BACKTEST_ID = UUID("069a7ea6-5737-47bd-b7f2-4dce09d4a91e")
        THRESHOLDS = [0.08, 0.10, 0.12]
        RESERVES = [0.02, 0.03]
        COOLDOWNS = [43200, 86400]

        def serialize(value):
            if isinstance(value, Decimal):
                return float(value)
            if isinstance(value, dict):
                return {key: serialize(item) for key, item in value.items()}
            if isinstance(value, list):
                return [serialize(item) for item in value]
            return value

        async def main():
            async with async_session_factory() as session:
                backtest = (
                    await session.execute(select(Backtest).where(Backtest.id == BACKTEST_ID))
                ).scalar_one()
                strategy = (
                    await session.execute(
                        select(Strategy).where(Strategy.id == backtest.strategy_id)
                    )
                ).scalar_one()

                base_request = BacktestRequest(
                    symbol=backtest.symbol,
                    venue=backtest.venue,
                    timeframe=backtest.timeframe,
                    start_time=backtest.start_time,
                    end_time=backtest.end_time,
                    initial_balance=extract_trading_config(backtest.strategy_config)[
                        "initial_balance"
                    ],
                    strategy_module=strategy.module_path,
                    strategy_class=strategy.strategy_class,
                    config_class=strategy.config_class,
                    strategy_config=backtest.strategy_config,
                    strategy_config_path=strategy.config_path,
                    backtest_id=str(backtest.id),
                    backtest_name=backtest.name,
                )

                pairs = NautilusAdapter._extract_strategy_symbols(base_request, {})  # noqa: SLF001
                loader = DataLoader(session)
                market_data, source = await loader.load_multi_data(
                    symbols=pairs,
                    venue=backtest.venue,
                    timeframe=backtest.timeframe,
                    start_time=backtest.start_time,
                    end_time=backtest.end_time,
                )

                adapter = NautilusAdapter(session=session)
                rows = []
                for threshold in THRESHOLDS:
                    for reserve in RESERVES:
                        for cooldown in COOLDOWNS:
                            strategy_config = deepcopy(backtest.strategy_config)
                            params = strategy_config.setdefault("parameters", {})
                            params["rebalance_threshold"] = threshold
                            params["cash_reserve_ratio"] = reserve
                            params["min_cooldown_secs"] = cooldown

                            request = BacktestRequest(
                                symbol=base_request.symbol,
                                venue=base_request.venue,
                                timeframe=base_request.timeframe,
                                start_time=base_request.start_time,
                                end_time=base_request.end_time,
                                initial_balance=base_request.initial_balance,
                                strategy_module=base_request.strategy_module,
                                strategy_class=base_request.strategy_class,
                                config_class=base_request.config_class,
                                strategy_config=strategy_config,
                                strategy_config_path=base_request.strategy_config_path,
                                backtest_id=f"{base_request.backtest_id}-sweep",
                                backtest_name=(
                                    f"rebalancing sweep thr={threshold} reserve={reserve} cd={cooldown}"
                                ),
                            )

                            result = await adapter.run(request=request, data=market_data)
                            rows.append(
                                {
                                    "rebalance_threshold": threshold,
                                    "cash_reserve_ratio": reserve,
                                    "min_cooldown_secs": cooldown,
                                    "final_balance": float(result.final_balance),
                                    "total_return_pct": float(result.total_return_pct),
                                    "max_drawdown_pct": float(result.max_drawdown_pct),
                                    "total_trades": int(result.total_trades),
                                    "sharpe_ratio": float(result.sharpe_ratio or 0),
                                    "profit_factor": float(result.profit_factor or 0),
                                    "full_metrics": serialize(result.full_metrics),
                                }
                            )

                print(
                    json.dumps(
                        {
                            "backtest_id": str(backtest.id),
                            "data_source": source,
                            "rows": rows,
                        },
                        ensure_ascii=False,
                        indent=2,
                    )
                )

        asyncio.run(main())
        """
    ).strip()


def _render_markdown(rows: list[dict]) -> str:
    sorted_rows = sorted(rows, key=lambda item: item["total_return_pct"], reverse=True)
    header = [
        "# Rebalance Speculum Adapter Parameter Sweep",
        "",
        "## Scope",
        "",
        "- Dataset: speculum-aligned Binance Spot 1h BTC/ETH/SOL",
        "- Purpose: stage-2 real-data parameter polishing",
        "- Baseline source: backtest `069a7ea6-5737-47bd-b7f2-4dce09d4a91e`",
        "",
        "## Top Results",
        "",
        "| threshold | reserve | cooldown_secs | return_pct | max_dd_pct | trades | sharpe | profit_factor |",
        "| --- | --- | --- | --- | --- | --- | --- | --- |",
    ]
    for row in sorted_rows:
        header.append(
            "| {rebalance_threshold:.2f} | {cash_reserve_ratio:.2f} | {min_cooldown_secs} | "
            "{total_return_pct:.6f} | {max_drawdown_pct:.6f} | {total_trades} | "
            "{sharpe_ratio:.6f} | {profit_factor:.6f} |".format(**row)
        )
    header.append("")
    return "\n".join(header)


def run() -> dict:
    remote_script = _build_remote_script()
    command = [
        "docker",
        "exec",
        "speculum-backend",
        "sh",
        "-lc",
        f"cd /app && python - <<'PY'\n{remote_script}\nPY",
    ]
    completed = subprocess.run(command, capture_output=True, text=True, check=True)
    lines = [line for line in completed.stdout.splitlines() if not line.startswith("\x1b")]
    json_start = next(index for index, line in enumerate(lines) if line.lstrip().startswith("{"))
    payload = json.loads("\n".join(lines[json_start:]))

    JSON_PATH.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    rows = payload["rows"]
    with CSV_PATH.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=[
                "rebalance_threshold",
                "cash_reserve_ratio",
                "min_cooldown_secs",
                "final_balance",
                "total_return_pct",
                "max_drawdown_pct",
                "total_trades",
                "sharpe_ratio",
                "profit_factor",
            ],
        )
        writer.writeheader()
        for row in rows:
            writer.writerow({key: row[key] for key in writer.fieldnames})

    MD_PATH.write_text(_render_markdown(rows), encoding="utf-8")
    return payload


if __name__ == "__main__":
    result = run()
    print(json.dumps(result, ensure_ascii=False, indent=2))
    print(f"\nSaved sweep json to {JSON_PATH}")
    print(f"Saved sweep csv to {CSV_PATH}")
    print(f"Saved sweep report to {MD_PATH}")
