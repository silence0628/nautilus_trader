#!/usr/bin/env python3

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

import numpy as np
import yaml

REPO_ROOT = Path(__file__).resolve().parents[3]
WORKSPACE_ROOT = REPO_ROOT.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from formal_strategy import FormalRebalancingStrategy
from formal_strategy import ResearchAssetConfig
from formal_strategy import ResearchParametersConfig
from formal_strategy import ResearchRebalancingConfig

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


STONE_CONFIG_PATH = (
    WORKSPACE_ROOT
    / "philosophers-stone"
    / "portfolio"
    / "rebalancing"
    / "config.yaml"
)
RESULTS_DIR = Path(__file__).resolve().parent / "results"
RESULTS_DIR.mkdir(parents=True, exist_ok=True)
OUTPUT_PATH = RESULTS_DIR / "rebalance_formal_research_baseline_summary.json"


TIMEFRAME_MAP = {
    "1h": (1, BarAggregation.HOUR),
}


def _extract_values(node: Any) -> Any:
    if isinstance(node, dict):
        if "value" in node and "type" in node:
            return node["value"]
        result = {}
        for key, value in node.items():
            if key.startswith("_"):
                continue
            result[key] = _extract_values(value)
        return result
    if isinstance(node, list):
        return [_extract_values(item) for item in node]
    return node


def load_formal_research_config() -> dict[str, Any]:
    raw = yaml.safe_load(STONE_CONFIG_PATH.read_text(encoding="utf-8"))
    values = _extract_values(raw)
    trading = values["trading"]
    position = values["position"]
    platforms = values["platforms"]
    parameters = values["parameters"]
    venue = platforms["nautilus"]["venue"]
    bar_type = platforms["nautilus"]["bar_type"]
    bar_aggregation = platforms["nautilus"]["bar_aggregation"]

    assets = []
    for asset in parameters["assets"]:
        pair = asset["pair"]
        symbol = pair.replace("-", "")
        instrument_id = f"{symbol}.{venue}"
        assets.append(
            ResearchAssetConfig(
                pair=pair,
                weight=float(asset["weight"]),
                instrument_id=instrument_id,
                bar_type=f"{instrument_id}-{bar_type}-LAST-{bar_aggregation}",
            )
        )

    parameters_config = ResearchParametersConfig(
        assets=tuple(assets),
        rebalance_threshold=parameters["rebalance_threshold"],
        min_cooldown_secs=parameters["min_cooldown_secs"],
        cash_reserve_ratio=parameters["cash_reserve_ratio"],
        min_trade_value=parameters["min_trade_value"],
        max_single_trade_ratio=parameters["max_single_trade_ratio"],
        max_drawdown=parameters["max_drawdown"],
        max_daily_rebalances=parameters["max_daily_rebalances"],
        use_volatility_filter=parameters["use_volatility_filter"],
        volatility_lookback_bars=parameters["volatility_lookback_bars"],
        volatility_threshold_multiplier=parameters["volatility_threshold_multiplier"],
        use_trend_filter=parameters["use_trend_filter"],
        trend_lookback_bars=parameters["trend_lookback_bars"],
        trend_strength_buffer=parameters["trend_strength_buffer"],
        kill_switch_mode=parameters["kill_switch_mode"],
        kill_switch_resume_secs=parameters["kill_switch_resume_secs"],
        close_positions_on_stop=parameters["close_positions_on_stop"],
        post_resume_band_secs=parameters["post_resume_band_secs"],
        post_resume_throttle_secs=parameters["post_resume_throttle_secs"],
        post_resume_max_single_trade_ratio=parameters["post_resume_max_single_trade_ratio"],
    )

    return {
        "symbol": "BTCUSDT",
        "venue": venue,
        "timeframe": "1h",
        "start_time": "2022-12-31 00:00:00",
        "end_time": "2026-01-01 00:00:00",
        "trading": trading,
        "position": position,
        "platforms": platforms,
        "parameters": parameters_config,
    }


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
    portfolio_values = np.array([float(point["value"]) for point in portfolio_points], dtype=float)
    drawdown_values = np.array([float(point["value"]) for point in drawdown_points], dtype=float)

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
        "fills_count": len(engine.trader.generate_order_fills_report()),
        "positions_count": len(engine.trader.generate_positions_report()),
        "metrics": {
            "final_balance": float(portfolio_values[-1]),
            "peak_balance": float(np.max(portfolio_values)),
            "total_return_pct": float(((portfolio_values[-1] - initial_capital) / initial_capital) * 100),
            "max_drawdown_pct": float(np.max(drawdown_values) * 100),
            "equity_points": len(portfolio_points),
        },
    }
    OUTPUT_PATH.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    engine.dispose()
    return payload


if __name__ == "__main__":
    result = run()
    print(json.dumps(result, ensure_ascii=False, indent=2))
    print(f"\nSaved formal research baseline to {OUTPUT_PATH}")
