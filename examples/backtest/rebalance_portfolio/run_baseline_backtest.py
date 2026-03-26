#!/usr/bin/env python3

from __future__ import annotations

from pathlib import Path

import pandas as pd

from runner import run_rebalance_backtest


RESULTS_DIR = Path(__file__).resolve().parent / "results"
RESULTS_DIR.mkdir(parents=True, exist_ok=True)


def _render_summary_markdown(
    summary,
    account_report: pd.DataFrame,
    fills_report: pd.DataFrame,
    positions_report: pd.DataFrame,
) -> str:
    sections = [
        "# Rebalance Baseline Backtest Report",
        "",
        "## Summary",
        "",
        f"- Rebalance cycles: `{summary.rebalance_cycles}`",
        f"- Fill count: `{summary.fill_count}`",
        f"- Closed positions: `{summary.position_count}`",
        f"- Starting balance (USDT): `{summary.starting_balance}`",
        f"- Ending portfolio value (USDT): `{summary.ending_portfolio_value:.8f}`",
        f"- Total return (%): `{summary.total_return_pct:.4f}`",
        f"- Ending balances: `USDT={summary.ending_usdt:.8f}, BTC={summary.ending_btc:.8f}, ETH={summary.ending_eth:.8f}, SOL={summary.ending_sol:.8f}`",
        "",
        "## Account Report",
        "",
        account_report.to_markdown(index=False) if not account_report.empty else "_No account rows_",
        "",
        "## Fills Report",
        "",
        fills_report.to_markdown(index=False) if not fills_report.empty else "_No fills_",
        "",
        "## Positions Report",
        "",
        positions_report.to_markdown(index=False) if not positions_report.empty else "_No positions_",
        "",
    ]
    return "\n".join(sections)


if __name__ == "__main__":
    summary, account_report, fills_report, positions_report = run_rebalance_backtest(
        trader_id="REBALANCE-BASELINE-001",
        log_level="INFO",
    )

    report_path = RESULTS_DIR / "rebalance_baseline_report.md"
    report_path.write_text(
        _render_summary_markdown(summary, account_report, fills_report, positions_report),
        encoding="utf-8",
    )

    print(f"Saved baseline report to {report_path}")
    print(account_report.tail(5))
    print(fills_report.tail(10))
    print(positions_report.tail(10))
