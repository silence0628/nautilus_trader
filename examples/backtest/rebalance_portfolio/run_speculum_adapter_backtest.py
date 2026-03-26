#!/usr/bin/env python3

from __future__ import annotations

import json
import os
import textwrap
from pathlib import Path

from speculum_runtime import run_backend_python


RESULTS_DIR = Path(__file__).resolve().parent / "results"
RESULTS_DIR.mkdir(parents=True, exist_ok=True)
OUTPUT_PATH = RESULTS_DIR / "rebalance_speculum_adapter_backtest_summary.json"


def _build_remote_script() -> str:
    return textwrap.dedent(
        """
        import asyncio
        import json
        import os
        from decimal import Decimal
        from uuid import UUID

        from sqlalchemy import desc, select

        from app.db.base import async_session_factory
        from app.db.models.backtest import Backtest
        from app.db.models.strategy import Strategy
        from app.engine.adapters.nautilus.adapter import NautilusAdapter
        from app.engine.core.base import BacktestRequest
        from app.engine.data_loader import DataLoader
        from app.utils.config_extractor import extract_trading_config

        TARGET_BACKTEST_ID = os.getenv("REBALANCE_BACKTEST_ID", "").strip()

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
                if TARGET_BACKTEST_ID:
                    backtest = (
                        await session.execute(
                            select(Backtest).where(Backtest.id == UUID(TARGET_BACKTEST_ID))
                        )
                    ).scalar_one()
                    strategy = (
                        await session.execute(
                            select(Strategy).where(Strategy.id == backtest.strategy_id)
                        )
                    ).scalar_one()
                else:
                    rows = (
                        await session.execute(
                            select(Backtest, Strategy)
                            .join(Strategy, Strategy.id == Backtest.strategy_id)
                            .where(
                                Backtest.status == "COMPLETED",
                                Strategy.strategy_class == "RebalancingStrategy",
                            )
                            .order_by(desc(Backtest.created_at))
                        )
                    ).all()
                    if not rows:
                        raise RuntimeError("No completed rebalancing backtest found in speculum DB.")

                    preferred = None
                    for candidate_backtest, candidate_strategy in rows:
                        if "baseline" in (candidate_backtest.name or "").lower():
                            preferred = (candidate_backtest, candidate_strategy)
                            break
                    backtest, strategy = preferred or rows[0]

                request = BacktestRequest(
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

                pairs = NautilusAdapter._extract_strategy_symbols(request, {})  # noqa: SLF001
                loader = DataLoader(session)
                market_data, source = await loader.load_multi_data(
                    symbols=pairs,
                    venue=backtest.venue,
                    timeframe=backtest.timeframe,
                    start_time=backtest.start_time,
                    end_time=backtest.end_time,
                )

                adapter = NautilusAdapter(session=session)
                result = await adapter.run(request=request, data=market_data)

                payload = {
                    "backtest_id": str(backtest.id),
                    "name": backtest.name,
                    "data_source": source,
                    "result": {
                        "final_balance": float(result.final_balance),
                        "total_return": float(result.total_return),
                        "total_return_pct": float(result.total_return_pct),
                        "peak_balance": float(result.peak_balance or 0),
                        "max_drawdown_pct": float(result.max_drawdown_pct),
                        "total_trades": result.total_trades,
                        "sharpe_ratio": float(result.sharpe_ratio or 0),
                        "profit_factor": float(result.profit_factor or 0),
                        "indicators": len(result.indicators),
                        "equity_points": len(result.equity_curve),
                        "full_metrics": serialize(result.full_metrics),
                    },
                }
                print(json.dumps(payload, ensure_ascii=False, indent=2))

        asyncio.run(main())
        """
    ).strip()


def run() -> dict:
    remote_script = _build_remote_script()
    payload = run_backend_python(remote_script)
    OUTPUT_PATH.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return payload


if __name__ == "__main__":
    if os.getenv("REBALANCE_BACKTEST_ID"):
        print(f"Using explicit REBALANCE_BACKTEST_ID={os.getenv('REBALANCE_BACKTEST_ID')}")
    result = run()
    print(json.dumps(result, ensure_ascii=False, indent=2))
    print(f"\nSaved speculum-adapter summary to {OUTPUT_PATH}")
