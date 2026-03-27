#!/usr/bin/env python3

from __future__ import annotations

import json
import importlib.util
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
    step, aggregation = TIMEFRAME_MAP[context["timeframe"]]
    bar_spec = BarSpecification(step=step, aggregation=aggregation, price_type=PriceType.LAST)
    trading = context["trading"]
    initial_capital = float(context["position"]["initial_capital"])
    fees = trading.get("fees", {})
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
        fill_model=FillModel(),
        latency_model=LatencyModel(),
    )

    for pair in trading["pairs"]:
        symbol = pair.replace("-", "")
        df = _load_ohlcv(symbol, context["start_time"], context["end_time"])
        market_data[symbol] = df
        instrument = create_spot_instrument(
            symbol=symbol,
            venue=context["venue"],
            maker_fee=fees.get("maker", 0.0002),
            taker_fee=fees.get("taker", 0.0004),
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
    OUTPUT_PATH.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

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
    REPORT_PATH.write_text(report_markdown, encoding="utf-8")
    engine.dispose()
    return payload


if __name__ == "__main__":
    result = run()
    print(json.dumps(result, ensure_ascii=False, indent=2))
    print(f"\nSaved formal research baseline to {OUTPUT_PATH}")
    print(f"Saved formal research report to {REPORT_PATH}")
