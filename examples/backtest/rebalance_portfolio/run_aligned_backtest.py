#!/usr/bin/env python3

from __future__ import annotations

import json
import sys
from dataclasses import asdict
from decimal import Decimal
from pathlib import Path


STONE_ROOT = Path("/Users/a111/Data/wukai/philosophers-stone")
if str(STONE_ROOT) not in sys.path:
    sys.path.insert(0, str(STONE_ROOT))

from nautilus_trader.backtest.engine import BacktestEngine, BacktestEngineConfig
from nautilus_trader.backtest.models import FillModel, LatencyModel
from nautilus_trader.config import LoggingConfig
from nautilus_trader.model.data import BarType
from nautilus_trader.model.enums import AccountType, OmsType
from nautilus_trader.model.identifiers import Venue
from nautilus_trader.model.objects import Currency, Money

from portfolio.rebalancing.refinement.nautilus.run_smoke_backtest import (
    DEFAULT_CONFIG_PATH,
    DEFAULT_CSV_DIR,
    SmokeBacktestSummary,
    _build_csv_instruments,
    _dataframe_to_bars,
    _dataframe_to_quotes,
    _format_summary,
    _instrument_id_for_pair,
    _load_symbol_csv,
    _normalize_symbol,
    _parse_dt,
)
from portfolio.rebalancing.refinement.nautilus.strategy import (
    RebalancingStrategy,
    RebalancingStrategyConfig,
    build_parameters_config,
)
from shared.config import load_config
from shared.nautilus import build_nautilus_base_config, get_venue_from_connector


RESULTS_DIR = Path(__file__).resolve().parent / "results"
RESULTS_DIR.mkdir(parents=True, exist_ok=True)
DEFAULT_START = "2023-01-01T00:00:00+00:00"
DEFAULT_END = "2026-01-01T00:00:00+00:00"


def run_aligned_backtest() -> SmokeBacktestSummary:
    config_path = DEFAULT_CONFIG_PATH.resolve()
    csv_dir = DEFAULT_CSV_DIR.resolve()
    start = _parse_dt(DEFAULT_START)
    end = _parse_dt(DEFAULT_END)

    wrapper = load_config(config_path)
    pairs = wrapper.trading.get("pairs", ["BTC-USDT", "ETH-USDT", "SOL-USDT"])
    connector = wrapper.trading.get("connector", "binance")
    fees = wrapper.trading.get("fees", {})
    venue_code = get_venue_from_connector(connector)
    maker_fee = Decimal(str(fees.get("maker", 0.001)))
    taker_fee = Decimal(str(fees.get("taker", 0.001)))
    bar_interval = wrapper.platforms.get("nautilus", {}).get("bar_type", "1-HOUR")
    initial_balance = float(wrapper.position.get("initial_capital", 10000.0))

    instruments = _build_csv_instruments(
        maker_fee=maker_fee,
        taker_fee=taker_fee,
    )

    engine = BacktestEngine(
        config=BacktestEngineConfig(
            logging=LoggingConfig(log_level="ERROR"),
        )
    )
    engine.add_venue(
        venue=Venue(venue_code),
        oms_type=OmsType.NETTING,
        account_type=AccountType.CASH,
        base_currency=None,
        starting_balances=[Money(initial_balance, Currency.from_str("USDT"))],
        fill_model=FillModel(),
        latency_model=LatencyModel(),
    )

    for pair in pairs:
        instrument_id = _instrument_id_for_pair(pair, connector)
        instrument = instruments[instrument_id]
        engine.add_instrument(instrument)

        full_bar_type = f"{instrument_id}-{bar_interval}-LAST-EXTERNAL"
        df = _load_symbol_csv(csv_dir, _normalize_symbol(pair), bar_interval)
        bar_type = BarType.from_str(full_bar_type)
        quotes = _dataframe_to_quotes(df, instrument, bar_type.spec)
        bars = _dataframe_to_bars(df, instrument, bar_type.spec)
        engine.add_data(quotes)
        engine.add_data(bars)

    parameters = build_parameters_config(wrapper)
    base_sections = build_nautilus_base_config(wrapper)
    strategy = RebalancingStrategy(
        RebalancingStrategyConfig(
            parameters=parameters,
            **base_sections,
        )
    )
    engine.add_strategy(strategy)
    engine.run(start=start, end=end)

    snapshot = strategy.get_snapshot_state()
    final_balance = float(snapshot.get("last_portfolio_value", "0") or 0.0)
    max_drawdown_pct = float(snapshot.get("max_observed_drawdown", "0") or 0.0) * 100

    summary = SmokeBacktestSummary(
        config_path=str(config_path),
        data_source=f"csv:{csv_dir}",
        start=start.isoformat(),
        end=end.isoformat(),
        initial_balance=initial_balance,
        final_balance=final_balance,
        return_pct=((final_balance - initial_balance) / initial_balance) * 100,
        max_drawdown_pct=max_drawdown_pct,
        total_orders=len(engine.trader.generate_order_fills_report()),
        total_positions=len(engine.trader.generate_positions_report()),
        total_rebalances=int(snapshot.get("total_rebalances", 0)),
        kill_switch_triggered_ts=snapshot.get("kill_switch_triggered_ts"),
    )
    engine.dispose()
    return summary


if __name__ == "__main__":
    summary = run_aligned_backtest()
    report_path = RESULTS_DIR / "rebalance_aligned_backtest_summary.json"
    report_path.write_text(json.dumps(asdict(summary), ensure_ascii=False, indent=2), encoding="utf-8")
    print(_format_summary(summary))
    print(f"\nSaved aligned summary to {report_path}")
