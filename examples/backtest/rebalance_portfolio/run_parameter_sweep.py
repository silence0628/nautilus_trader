#!/usr/bin/env python3

from __future__ import annotations

from decimal import Decimal

import pandas as pd

from reporting import dataframe_to_markdown
from runner import RESULTS_DIR
from runner import run_rebalance_backtest


def _render_markdown(results: pd.DataFrame) -> str:
    top = results.sort_values("ending_portfolio_value", ascending=False).head(10)
    return "\n".join(
        [
            "# Rebalance Parameter Sweep Report",
            "",
            "## Scope",
            "",
            "- Dataset: synthetic BTC/ETH/SOL hourly bars",
            "- Purpose: stage-004 exploratory parameter ranking",
            "- Note: results are only for technical research ordering, not production validity",
            "",
            "## Top 10 By Ending Portfolio Value",
            "",
            dataframe_to_markdown(top),
            "",
        ]
    )


if __name__ == "__main__":
    thresholds = [Decimal("0.08"), Decimal("0.10"), Decimal("0.12")]
    reserves = [Decimal("0.02"), Decimal("0.03")]
    cooldowns = [6, 12]
    rows: list[dict[str, object]] = []

    for threshold in thresholds:
        for reserve in reserves:
            for cooldown in cooldowns:
                trader_id = (
                    f"RB-SWEEP-{str(threshold).replace('.', '')}-"
                    f"{str(reserve).replace('.', '')}-{cooldown}"
                )
                summary, _, _, _ = run_rebalance_backtest(
                    trader_id=trader_id,
                    rebalance_threshold=threshold,
                    cash_reserve_ratio=reserve,
                    min_cooldown_bars=cooldown,
                    log_level="ERROR",
                )
                rows.append(
                    {
                        "trader_id": summary.trader_id,
                        "rebalance_threshold": str(summary.rebalance_threshold),
                        "cash_reserve_ratio": str(summary.cash_reserve_ratio),
                        "min_cooldown_bars": summary.min_cooldown_bars,
                        "rebalance_cycles": summary.rebalance_cycles,
                        "fill_count": summary.fill_count,
                        "ending_portfolio_value": float(summary.ending_portfolio_value),
                        "total_return_pct": float(summary.total_return_pct),
                    }
                )

    results = pd.DataFrame(rows).sort_values("ending_portfolio_value", ascending=False)
    csv_path = RESULTS_DIR / "rebalance_parameter_sweep.csv"
    md_path = RESULTS_DIR / "rebalance_parameter_sweep.md"
    results.to_csv(csv_path, index=False)
    md_path.write_text(_render_markdown(results), encoding="utf-8")

    print(f"Saved sweep csv to {csv_path}")
    print(f"Saved sweep report to {md_path}")
    print(results.head(10))
