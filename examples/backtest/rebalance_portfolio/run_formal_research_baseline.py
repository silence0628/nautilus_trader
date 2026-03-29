#!/usr/bin/env python3

from __future__ import annotations

import json
import importlib.util
import importlib
import os
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
import yaml

REPO_ROOT = Path(__file__).resolve().parents[3]
WORKSPACE_ROOT = REPO_ROOT.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))
SPECULUM_METRICS_PATH = (
    WORKSPACE_ROOT
    / "speculum"
    / "backend"
    / "app"
    / "engine"
    / "adapters"
    / "nautilus"
    / "metrics.py"
)

from reporting import build_equity_curve_dataframe
from reporting import build_equity_curve_dataframe_with_initial
from reporting import build_monthly_returns_from_indicator_points
from reporting import build_recent_trades_table
from reporting import calculate_performance_metrics
from reporting import calculate_trade_statistics
from reporting import render_formal_markdown_report


_metrics_spec = importlib.util.spec_from_file_location("speculum_nautilus_metrics", SPECULUM_METRICS_PATH)
if _metrics_spec is None or _metrics_spec.loader is None:
    raise RuntimeError(f"Unable to load Speculum metrics module from {SPECULUM_METRICS_PATH}")
_metrics_module = importlib.util.module_from_spec(_metrics_spec)
_metrics_spec.loader.exec_module(_metrics_module)
calculate_advanced_metrics = _metrics_module.calculate_advanced_metrics


RESULTS_DIR = Path(__file__).resolve().parent / "results"
RESULTS_DIR.mkdir(parents=True, exist_ok=True)
OUTPUT_PATH = RESULTS_DIR / "rebalance_formal_research_baseline_summary.json"
REPORT_PATH = RESULTS_DIR / "backtest_report_BTCUSDT_ETHUSDT_SOLUSDT_1h_formal_research_baseline.md"


def _env_float(name: str, default: float) -> float:
    raw = os.getenv(name)
    if raw is None or raw.strip() == "":
        return default
    return float(raw)


def _scenario_paths() -> tuple[Path, Path, str]:
    scenario = os.getenv("REBALANCE_SCENARIO_NAME", "").strip()
    if not scenario:
        return OUTPUT_PATH, REPORT_PATH, "baseline"

    safe = "".join(ch if ch.isalnum() or ch in ("-", "_") else "_" for ch in scenario)
    output = RESULTS_DIR / f"rebalance_formal_research_baseline_summary_{safe}.json"
    report = RESULTS_DIR / (
        f"backtest_report_BTCUSDT_ETHUSDT_SOLUSDT_1h_formal_research_baseline_{safe}.md"
    )
    return output, report, safe


def _parse_semver(value: str) -> tuple[int, int, int]:
    parts = value.strip().split(".")
    normalized = []
    for part in parts[:3]:
        digits = "".join(ch for ch in part if ch.isdigit())
        normalized.append(int(digits or 0))
    while len(normalized) < 3:
        normalized.append(0)
    return tuple(normalized)


def _read_required_rust_version() -> str | None:
    toolchain_path = REPO_ROOT / "rust-toolchain.toml"
    if not toolchain_path.exists():
        return None
    for line in toolchain_path.read_text(encoding="utf-8").splitlines():
        if line.strip().startswith("version"):
            return line.split("=", 1)[1].strip().strip('"')
    return None


def _read_local_rust_version() -> str | None:
    try:
        completed = subprocess.run(
            ["rustc", "--version"],
            capture_output=True,
            text=True,
            check=True,
        )
    except (FileNotFoundError, subprocess.CalledProcessError):
        return None
    parts = completed.stdout.strip().split()
    return parts[1] if len(parts) >= 2 else None


def _ensure_nautilus_runtime_ready() -> None:
    required_rust = _read_required_rust_version()
    local_rust = _read_local_rust_version()

    try:
        importlib.import_module("nautilus_trader.core.data")
    except ModuleNotFoundError as exc:
        hints = [
            "Formal research baseline requires Nautilus compiled extensions to be available.",
            f"Current repo path: {REPO_ROOT}",
        ]
        if local_rust and required_rust and _parse_semver(local_rust) < _parse_semver(required_rust):
            hints.append(
                f"Current rustc is {local_rust}, but this repo requires at least {required_rust}."
            )
            hints.append("Update Rust toolchain first, then rebuild/install nautilus_trader.")
        elif required_rust:
            hints.append(f"Required Rust toolchain version: {required_rust}.")

        current_python = Path(sys.executable)
        if ".venv" in current_python.parts or os.environ.get("VIRTUAL_ENV"):
            hints.append(
                f"Current interpreter is {current_python}, but it is still missing the compiled nautilus_trader core modules."
            )
        else:
            hints.append(
                f"Current interpreter is {current_python}; no prepared nautilus virtualenv with compiled core modules was detected."
            )

        hints.append(
            "Fallback available now: keep using run_speculum_adapter_backtest.py for aligned research validation."
        )
        raise RuntimeError("\n".join(hints)) from exc


_ensure_nautilus_runtime_ready()

from configs.loader import build_research_context
from strategy_core.rebalance_strategy import FormalRebalancingStrategy
from strategy_core.rebalance_strategy import ResearchRebalancingConfig

from nautilus_trader.backtest.engine import BacktestEngine
from nautilus_trader.backtest.models import FillModel
from nautilus_trader.backtest.models import LatencyModel
from nautilus_trader.config import BacktestEngineConfig
from nautilus_trader.config import LoggingConfig
from nautilus_trader.model.data import Bar
from nautilus_trader.model.data import BarSpecification
from nautilus_trader.model.data import QuoteTick
from nautilus_trader.model.enums import AccountType
from nautilus_trader.model.enums import AggregationSource
from nautilus_trader.model.enums import BarAggregation
from nautilus_trader.model.enums import OmsType
from nautilus_trader.model.enums import PriceType
from nautilus_trader.model.identifiers import Venue
from nautilus_trader.model.objects import Currency
from nautilus_trader.model.objects import Money
from run_speculum_backtest_node import _load_ohlcv
from run_speculum_backtest_node import create_spot_instrument


TIMEFRAME_MAP = {
    "1h": (1, BarAggregation.HOUR),
}

def load_formal_research_config() -> dict[str, Any]:
    return build_research_context("baseline")


def _dataframe_to_bars(df, instrument, bar_spec):
    from nautilus_trader.model.data import BarType

    bar_type = BarType(
        instrument_id=instrument.id,
        bar_spec=bar_spec,
        aggregation_source=AggregationSource.EXTERNAL,
    )
    duration_ns = bar_spec.timedelta.total_seconds() * 1_000_000_000
    bars: list[Bar] = []
    for _, row in df.iterrows():
        open_ts_ns = row["timestamp"].value
        close_ts_ns = int(open_ts_ns + duration_ns)
        bars.append(
            Bar(
                bar_type=bar_type,
                open=instrument.make_price(row["open"]),
                high=instrument.make_price(row["high"]),
                low=instrument.make_price(row["low"]),
                close=instrument.make_price(row["close"]),
                volume=instrument.make_qty(row["volume"]) if row["volume"] > 0 else instrument.make_qty(1),
                ts_event=close_ts_ns,
                ts_init=close_ts_ns,
            )
        )
    return bars


def _dataframe_to_quotes(df, instrument, bar_spec):
    duration_ns = bar_spec.timedelta.total_seconds() * 1_000_000_000
    quotes: list[QuoteTick] = []
    for _, row in df.iterrows():
        open_ts_ns = row["timestamp"].value
        close_ts_ns = int(open_ts_ns + duration_ns)
        quote_ts_ns = close_ts_ns - 1
        price = instrument.make_price(row["open"])
        quotes.append(
            QuoteTick(
                instrument_id=instrument.id,
                bid_price=price,
                ask_price=price,
                bid_size=instrument.make_qty(1),
                ask_size=instrument.make_qty(1),
                ts_event=quote_ts_ns,
                ts_init=quote_ts_ns,
            )
        )
    return quotes


def run() -> dict[str, Any]:
    context = load_formal_research_config()
    output_path, report_path, scenario_name = _scenario_paths()
    step, aggregation = TIMEFRAME_MAP[context["timeframe"]]
    bar_spec = BarSpecification(step=step, aggregation=aggregation, price_type=PriceType.LAST)
    trading = context["trading"]
    initial_capital = float(context["position"]["initial_capital"])
    fees = trading.get("fees", {})
    maker_fee = _env_float("REBALANCE_MAKER_FEE", float(fees.get("maker", 0.0002)))
    taker_fee = _env_float("REBALANCE_TAKER_FEE", float(fees.get("taker", 0.0004)))
    prob_fill_on_limit = _env_float("REBALANCE_PROB_FILL_ON_LIMIT", 1.0)
    prob_slippage = _env_float("REBALANCE_PROB_SLIPPAGE", 0.0)
    market_data: dict[str, pd.DataFrame] = {}

    engine = BacktestEngine(
        config=BacktestEngineConfig(
            logging=LoggingConfig(log_level="WARNING"),
        ),
    )
    engine.add_venue(
        venue=Venue(context["venue"]),
        oms_type=OmsType.NETTING,
        account_type=AccountType.CASH,
        base_currency=None,
        starting_balances=[Money(initial_capital, Currency.from_str("USDT"))],
        fill_model=FillModel(
            prob_fill_on_limit=prob_fill_on_limit,
            prob_slippage=prob_slippage,
        ),
        latency_model=LatencyModel(),
    )

    for pair in trading["pairs"]:
        symbol = pair.replace("-", "")
        df = _load_ohlcv(symbol, context["start_time"], context["end_time"])
        market_data[symbol] = df
        instrument = create_spot_instrument(
            symbol=symbol,
            venue=context["venue"],
            maker_fee=maker_fee,
            taker_fee=taker_fee,
        )
        engine.add_instrument(instrument)
        engine.add_data(_dataframe_to_quotes(df, instrument, bar_spec))
        engine.add_data(_dataframe_to_bars(df, instrument, bar_spec))

    strategy = FormalRebalancingStrategy(
        config=ResearchRebalancingConfig(
            trading=context["trading"],
            position=context["position"],
            platforms=context["platforms"],
            parameters=context["parameters"],
        )
    )
    engine.add_strategy(strategy)
    engine.run()

    portfolio_points = strategy.get_indicator_history()["portfolio_value"]["points"]
    drawdown_points = strategy.get_indicator_history()["drawdown"]["points"]
    account_report = engine.trader.generate_account_report(Venue(context["venue"]))
    fills_report = engine.trader.generate_order_fills_report()
    positions_report = engine.trader.generate_positions_report()
    portfolio_values = np.array([float(point["value"]) for point in portfolio_points], dtype=float)
    drawdown_values = np.array([float(point["value"]) for point in drawdown_points], dtype=float)
    equity_df = build_equity_curve_dataframe_with_initial(
        portfolio_points,
        initial_timestamp=pd.Timestamp(context["start_time"], tz=UTC),
        initial_value=initial_capital,
    )
    fallback_metrics = calculate_performance_metrics(
        equity_df=equity_df,
        initial_capital=initial_capital,
        max_drawdown_pct=float(np.max(drawdown_values) * 100),
    )
    team_metrics = calculate_advanced_metrics(
        account_df=account_report,
        positions_df=positions_report,
        initial_balance=initial_capital,
        trading_days=(pd.Timestamp(context["end_time"]) - pd.Timestamp(context["start_time"])).days,
        market_data=market_data,
        quote_currency="USDT",
    )
    trade_stats = calculate_trade_statistics(positions_report)
    monthly_returns = build_monthly_returns_from_indicator_points(
        portfolio_points,
        initial_timestamp=context["start_time"],
        initial_value=initial_capital,
    )
    recent_trades = build_recent_trades_table(positions_report)

    strategy_config = {
        "trading": context["trading"],
        "position": context["position"],
        "parameters": {
            "assets": [
                {"pair": asset.pair, "weight": asset.weight}
                for asset in context["parameters"].assets
            ],
            "rebalance_threshold": context["parameters"].rebalance_threshold,
            "min_cooldown_secs": context["parameters"].min_cooldown_secs,
            "cash_reserve_ratio": context["parameters"].cash_reserve_ratio,
            "min_trade_value": context["parameters"].min_trade_value,
            "max_single_trade_ratio": context["parameters"].max_single_trade_ratio,
            "max_drawdown": context["parameters"].max_drawdown,
            "max_daily_rebalances": context["parameters"].max_daily_rebalances,
            "use_volatility_filter": context["parameters"].use_volatility_filter,
            "volatility_lookback_bars": context["parameters"].volatility_lookback_bars,
            "volatility_threshold_multiplier": context["parameters"].volatility_threshold_multiplier,
            "use_trend_filter": context["parameters"].use_trend_filter,
            "trend_lookback_bars": context["parameters"].trend_lookback_bars,
            "trend_strength_buffer": context["parameters"].trend_strength_buffer,
            "kill_switch_mode": context["parameters"].kill_switch_mode,
            "kill_switch_resume_secs": context["parameters"].kill_switch_resume_secs,
            "close_positions_on_stop": context["parameters"].close_positions_on_stop,
            "post_resume_band_secs": context["parameters"].post_resume_band_secs,
            "post_resume_throttle_secs": context["parameters"].post_resume_throttle_secs,
            "post_resume_max_single_trade_ratio": context["parameters"].post_resume_max_single_trade_ratio,
        },
    }

    payload = {
        "mode": "formal_research_baseline",
        "scenario": scenario_name,
        "symbol": context["symbol"],
        "venue": context["venue"],
        "timeframe": context["timeframe"],
        "start_time": context["start_time"],
        "end_time": context["end_time"],
        "pairs": trading["pairs"],
        "weights": [
            {"pair": asset.pair, "weight": asset.weight}
            for asset in context["parameters"].assets
        ],
        "kill_switch_mode": context["parameters"].kill_switch_mode,
        "execution_assumptions": {
            "maker_fee": maker_fee,
            "taker_fee": taker_fee,
            "prob_fill_on_limit": prob_fill_on_limit,
            "prob_slippage": prob_slippage,
        },
        "fills_count": len(fills_report),
        "positions_count": len(positions_report),
        "metrics": {
            "final_balance": float(portfolio_values[-1]),
            "peak_balance": float(np.max(portfolio_values)),
            "total_return_pct": float(((portfolio_values[-1] - initial_capital) / initial_capital) * 100),
            "max_drawdown_pct": float(np.max(drawdown_values) * 100),
            "equity_points": len(portfolio_points),
        },
        "report_metrics": {
            **fallback_metrics,
            "annualized_return_pct": team_metrics.get(
                "annualized_return", fallback_metrics["annualized_return_pct"]
            ),
            "volatility_pct": team_metrics.get("volatility", fallback_metrics["volatility_pct"]),
            "sharpe_ratio": team_metrics.get("sharpe_ratio", fallback_metrics["sharpe_ratio"]),
            "sortino_ratio": team_metrics.get("sortino_ratio", fallback_metrics["sortino_ratio"]),
            "calmar_ratio": team_metrics.get("calmar_ratio", fallback_metrics["calmar_ratio"]),
            **trade_stats,
        },
    }
    output_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    report_markdown = render_formal_markdown_report(
        title=f"{', '.join(pair.replace('-', '') for pair in context['trading']['pairs'])} {context['timeframe']} Backtest",
        generated_at=datetime.now(tz=UTC),
        context={
            "pairs": [pair.replace("-", "") for pair in context["trading"]["pairs"]],
            "venue": context["venue"],
            "timeframe": context["timeframe"],
            "start_time": pd.Timestamp(context["start_time"], tz=UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "end_time": pd.Timestamp(context["end_time"], tz=UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "initial_capital": initial_capital,
        },
        strategy_config=strategy_config,
        metrics={
            "net_profit": fallback_metrics["net_profit"],
            "total_return_pct": payload["metrics"]["total_return_pct"],
            "annualized_return_pct": payload["report_metrics"]["annualized_return_pct"],
            "max_drawdown_pct": payload["metrics"]["max_drawdown_pct"],
            "final_balance": payload["metrics"]["final_balance"],
            "sharpe_ratio": payload["report_metrics"]["sharpe_ratio"],
            "sortino_ratio": payload["report_metrics"]["sortino_ratio"],
            "calmar_ratio": payload["report_metrics"]["calmar_ratio"],
            "volatility_pct": payload["report_metrics"]["volatility_pct"],
        },
        trade_stats=trade_stats,
        monthly_returns=monthly_returns,
        recent_trades=recent_trades,
    )
    report_path.write_text(report_markdown, encoding="utf-8")
    engine.dispose()
    return payload


if __name__ == "__main__":
    result = run()
    print(json.dumps(result, ensure_ascii=False, indent=2))
    output_path, report_path, _ = _scenario_paths()
    print(f"\nSaved formal research baseline to {output_path}")
    print(f"Saved formal research report to {report_path}")
