#!/usr/bin/env python3

from __future__ import annotations

import json
import os
import subprocess
import sys
import textwrap
from decimal import Decimal
from io import StringIO
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

REPO_ROOT = Path(__file__).resolve().parents[3]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from nautilus_trader.backtest.node import BacktestNode
from nautilus_trader.config import BacktestDataConfig
from nautilus_trader.config import BacktestEngineConfig
from nautilus_trader.config import BacktestRunConfig
from nautilus_trader.config import BacktestVenueConfig
from nautilus_trader.config import ImportableStrategyConfig
from nautilus_trader.config import LoggingConfig
from nautilus_trader.model.data import Bar
from nautilus_trader.model.data import BarSpecification
from nautilus_trader.model.data import QuoteTick
from nautilus_trader.model.enums import AggregationSource
from nautilus_trader.model.enums import BarAggregation
from nautilus_trader.model.identifiers import InstrumentId
from nautilus_trader.model.identifiers import Symbol
from nautilus_trader.model.identifiers import Venue
from nautilus_trader.model.instruments import CurrencyPair
from nautilus_trader.model.objects import Currency
from nautilus_trader.model.objects import Money
from nautilus_trader.model.objects import Price
from nautilus_trader.model.objects import Quantity
from nautilus_trader.persistence.catalog.parquet import ParquetDataCatalog
from speculum_runtime import discover_stone_root
from speculum_runtime import run_backend_python


STONE_ROOT = discover_stone_root()
if str(STONE_ROOT) not in sys.path:
    sys.path.insert(0, str(STONE_ROOT))


RESULTS_DIR = Path(__file__).resolve().parent / "results"
RESULTS_DIR.mkdir(parents=True, exist_ok=True)
CATALOG_DIR = Path(__file__).resolve().parent / "catalogs" / "speculum_rebalance"
OUTPUT_PATH = RESULTS_DIR / "rebalance_speculum_backtest_node_summary.json"
TARGET_BACKTEST_ID = os.getenv("REBALANCE_BACKTEST_ID", "").strip()
SPECULUM_DB_CONTAINER = "speculum-postgres"


def _resolve_currency(code: str) -> Currency:
    return Currency.from_internal_map(code) or Currency.from_str(code)


def create_spot_instrument(symbol: str, venue: str, maker_fee: float, taker_fee: float) -> CurrencyPair:
    for suffix in ("USDT", "USDC", "BUSD", "TUSD", "FDUSD", "USD", "BTC", "ETH", "BNB"):
        if symbol.endswith(suffix) and len(symbol) > len(suffix):
            base_code = symbol[: -len(suffix)]
            quote_code = suffix
            break
    else:
        base_code = symbol
        quote_code = "USDT"

    base_currency = _resolve_currency(base_code)
    quote_currency = _resolve_currency(quote_code)
    instrument_id = InstrumentId.from_str(f"{symbol}.{venue}")
    return CurrencyPair(
        instrument_id=instrument_id,
        raw_symbol=Symbol(symbol),
        base_currency=base_currency,
        quote_currency=quote_currency,
        price_precision=2,
        size_precision=5,
        price_increment=Price.from_str("0.01"),
        size_increment=Quantity.from_str("0.00001"),
        lot_size=Quantity.from_str("0.00001"),
        max_quantity=Quantity.from_str("10000"),
        min_quantity=Quantity.from_str("0.00001"),
        max_notional=None,
        min_notional=Money(10.0, quote_currency),
        max_price=Price.from_str("1000000"),
        min_price=Price.from_str("0.01"),
        margin_init=Decimal("0"),
        margin_maint=Decimal("0"),
        maker_fee=Decimal(str(maker_fee)),
        taker_fee=Decimal(str(taker_fee)),
        ts_event=0,
        ts_init=0,
    )


def _extract_equity_series(account_df: pd.DataFrame, market_data: dict[str, pd.DataFrame], quote_currency: str = "USDT") -> list[dict[str, Any]]:
    if account_df.empty or "total" not in account_df.columns:
        return []

    working = account_df.copy()
    timestamp_col = "ts_event" if "ts_event" in working.columns else working.index.name or working.columns[0]
    if timestamp_col not in working.columns:
        working = working.reset_index()
    working["_timestamp"] = pd.to_datetime(working[timestamp_col], utc=True, errors="coerce")
    working["_total"] = pd.to_numeric(working["total"], errors="coerce")
    if "currency" in working.columns:
        working["_currency"] = working["currency"].astype(str).str.upper()
    else:
        working["_currency"] = quote_currency
    working = working.dropna(subset=["_timestamp", "_total", "_currency"]).sort_values("_timestamp")
    if working.empty:
        return []

    price_lookup: dict[str, tuple[np.ndarray, np.ndarray]] = {}
    for symbol, df in market_data.items():
        normalized = symbol.replace("-", "").replace("/", "").upper()
        if not normalized.endswith(quote_currency):
            continue
        base = normalized[: -len(quote_currency)]
        ts_ns = pd.to_datetime(df["timestamp"], utc=True).astype("int64").to_numpy()
        closes = pd.to_numeric(df["close"], errors="coerce").to_numpy(dtype=float)
        price_lookup[base] = (ts_ns, closes)

    equity_points: list[dict[str, Any]] = []
    for timestamp, snapshot in working.groupby("_timestamp", sort=True):
        balances: dict[str, float] = {}
        latest_by_currency = snapshot.groupby("_currency", sort=False).tail(1)
        for _, row in latest_by_currency.iterrows():
            balances[row["_currency"]] = float(row["_total"])

        equity = 0.0
        ts_ns = pd.Timestamp(timestamp).value
        for currency, amount in balances.items():
            if currency == quote_currency:
                equity += amount
                continue
            lookup = price_lookup.get(currency)
            if lookup is None:
                continue
            idx = np.searchsorted(lookup[0], ts_ns, side="right") - 1
            if idx >= 0:
                equity += amount * float(lookup[1][idx])
        equity_points.append({"timestamp": timestamp.isoformat(), "equity": equity})

    return equity_points


def _run_psql_copy(query: str) -> str:
    cmd = [
        "docker",
        "exec",
        SPECULUM_DB_CONTAINER,
        "psql",
        "-U",
        "speculum",
        "-d",
        "speculum",
        "-c",
        f"COPY ({query}) TO STDOUT WITH CSV HEADER",
    ]
    completed = subprocess.run(cmd, capture_output=True, text=True, check=True)
    return completed.stdout


def _load_speculum_context() -> dict[str, Any]:
    script = textwrap.dedent(
        f"""
        import asyncio
        import json
        import os
        from decimal import Decimal
        from uuid import UUID

        from sqlalchemy import desc, select

        from app.db.base import async_session_factory
        from app.db.models.backtest import Backtest
        from app.db.models.strategy import Strategy
        from app.engine.adapters.nautilus.config_builder import build_strategy_config

        TARGET_BACKTEST_ID = {TARGET_BACKTEST_ID!r}

        def serialize(value):
            if isinstance(value, Decimal):
                return float(value)
            if isinstance(value, dict):
                return {{key: serialize(item) for key, item in value.items()}}
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

                built = build_strategy_config(
                    strategy_module_path=strategy.module_path,
                    strategy_config_class=strategy.config_class,
                    yaml_config_path=f"/app/philosophers_stone/{{strategy.config_path}}",
                    ui_overrides=backtest.strategy_config,
                    symbol=backtest.symbol,
                    venue=backtest.venue,
                    timeframe=backtest.timeframe,
                )

                payload = {{
                    "backtest_id": str(backtest.id),
                    "name": backtest.name,
                    "symbol": backtest.symbol,
                    "venue": backtest.venue,
                    "timeframe": backtest.timeframe,
                    "start_time": backtest.start_time.isoformat(),
                    "end_time": backtest.end_time.isoformat(),
                    "strategy_module": strategy.module_path,
                    "strategy_class": strategy.strategy_class,
                    "config_class": strategy.config_class,
                    "built_config": built.json_primitives(),
                    "raw_strategy_config": serialize(backtest.strategy_config),
                }}
                print(json.dumps(payload, ensure_ascii=False, indent=2))

        asyncio.run(main())
        """
    ).strip()
    return run_backend_python(script)


def _load_ohlcv(symbol: str, start: str, end: str) -> pd.DataFrame:
    sql = f"""
        SELECT timestamp, open, high, low, close, volume
        FROM ohlcv_data
        WHERE dataset_id = (
            SELECT id
            FROM market_datasets
            WHERE symbol = '{symbol}'
              AND venue = 'BINANCE'
              AND timeframe = '1h'
            LIMIT 1
        )
          AND timestamp >= '{start}'
          AND timestamp <= '{end}'
        ORDER BY timestamp
    """
    csv_text = _run_psql_copy(" ".join(sql.split()))
    df = pd.read_csv(StringIO(csv_text))
    df["timestamp"] = pd.to_datetime(df["timestamp"], utc=True)
    return df


def _get_bar_duration_ns(bar_spec: BarSpecification) -> int:
    aggregation = bar_spec.aggregation
    step = bar_spec.step
    if aggregation == BarAggregation.MINUTE:
        seconds = step * 60
    elif aggregation == BarAggregation.HOUR:
        seconds = step * 3600
    elif aggregation == BarAggregation.DAY:
        seconds = step * 86400
    else:
        seconds = 3600
    return seconds * 1_000_000_000


def _dataframe_to_bars(df: pd.DataFrame, instrument, bar_spec: BarSpecification) -> list[Bar]:
    from nautilus_trader.model.data import BarType

    bar_type = BarType(
        instrument_id=instrument.id,
        bar_spec=bar_spec,
        aggregation_source=AggregationSource.EXTERNAL,
    )
    bar_duration_ns = _get_bar_duration_ns(bar_spec)
    bars: list[Bar] = []
    for _, row in df.iterrows():
        open_ts_ns = pd.Timestamp(row["timestamp"]).value
        close_ts_ns = open_ts_ns + bar_duration_ns
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


def _dataframe_to_quotes(df: pd.DataFrame, instrument, bar_spec: BarSpecification) -> list[QuoteTick]:
    bar_duration_ns = _get_bar_duration_ns(bar_spec)
    quotes: list[QuoteTick] = []
    for _, row in df.iterrows():
        open_ts_ns = pd.Timestamp(row["timestamp"]).value
        close_ts_ns = open_ts_ns + bar_duration_ns
        quote_ts_ns = close_ts_ns - 1
        open_price = instrument.make_price(row["open"])
        quotes.append(
            QuoteTick(
                instrument_id=instrument.id,
                bid_price=open_price,
                ask_price=open_price,
                bid_size=instrument.make_qty(1),
                ask_size=instrument.make_qty(1),
                ts_event=quote_ts_ns,
                ts_init=quote_ts_ns,
            )
        )
    return quotes


def _prepare_catalog(context: dict[str, Any]) -> tuple[ParquetDataCatalog, list[str]]:
    if CATALOG_DIR.exists():
        shutil.rmtree(CATALOG_DIR)
    CATALOG_DIR.mkdir(parents=True, exist_ok=True)

    catalog = ParquetDataCatalog(str(CATALOG_DIR))
    trading = context["built_config"]["trading"]
    fees = trading.get("fees", {})
    maker_fee = fees.get("maker", 0.0002)
    taker_fee = fees.get("taker", 0.0004)
    pairs = trading["pairs"]
    start = context["start_time"].replace("T", " ").replace("+00:00", "")
    end = context["end_time"].replace("T", " ").replace("+00:00", "")

    stored_symbols: list[str] = []
    for pair in pairs:
        symbol = pair.replace("-", "")
        instrument = create_spot_instrument(
            symbol=symbol,
            venue=context["venue"],
            maker_fee=maker_fee,
            taker_fee=taker_fee,
        )
        bar_spec = BarSpecification.from_str("1-HOUR-LAST")
        df = _load_ohlcv(symbol, start, end)
        catalog.write_data([instrument], skip_disjoint_check=True)
        catalog.write_data(_dataframe_to_quotes(df, instrument, bar_spec), skip_disjoint_check=True)
        catalog.write_data(_dataframe_to_bars(df, instrument, bar_spec), skip_disjoint_check=True)
        stored_symbols.append(symbol)

    return catalog, stored_symbols


def _build_node(context: dict[str, Any], catalog: ParquetDataCatalog) -> tuple[BacktestNode, BacktestRunConfig]:
    built_config = context["built_config"]
    trading = built_config["trading"]
    initial_capital = built_config["position"]["initial_capital"]
    pairs = trading["pairs"]

    strategies = [
        ImportableStrategyConfig(
            strategy_path=f'{context["strategy_module"]}:{context["strategy_class"]}',
            config_path=f'{context["strategy_module"]}:{context["config_class"]}',
            config=built_config,
        )
    ]

    engine_config = BacktestEngineConfig(
        strategies=strategies,
        logging=LoggingConfig(log_level="ERROR"),
    )

    data: list[BacktestDataConfig] = []
    for pair in pairs:
        instrument_id = InstrumentId.from_str(f'{pair.replace("-", "")}.{context["venue"]}')
        data.append(
            BacktestDataConfig(
                catalog_path=str(catalog.path),
                data_cls=QuoteTick.fully_qualified_name(),
                instrument_id=instrument_id,
                start_time=context["start_time"],
                end_time=context["end_time"],
            )
        )
        data.append(
            BacktestDataConfig(
                catalog_path=str(catalog.path),
                data_cls=Bar.fully_qualified_name(),
                instrument_id=instrument_id,
                bar_spec="1-HOUR-LAST",
                start_time=context["start_time"],
                end_time=context["end_time"],
            )
        )

    venues = [
        BacktestVenueConfig(
            name=context["venue"],
            oms_type="NETTING",
            account_type="CASH",
            base_currency=None,
            starting_balances=[f"{initial_capital} USDT"],
            bar_execution=False,
        )
    ]

    run_config = BacktestRunConfig(
        engine=engine_config,
        data=data,
        venues=venues,
        chunk_size=None,
        raise_exception=True,
        dispose_on_completion=False,
        start=context["start_time"],
        end=context["end_time"],
    )

    return BacktestNode(configs=[run_config]), run_config


def run() -> dict[str, Any]:
    context = _load_speculum_context()
    catalog, stored_symbols = _prepare_catalog(context)
    node, run_config = _build_node(context, catalog)
    results = node.run()
    engine = node.get_engine(run_config.id)
    if engine is None:
        raise RuntimeError("BacktestNode did not retain engine instance for result extraction")

    account_df = engine.trader.generate_account_report(Venue(context["venue"]))
    equity_series = _extract_equity_series(
        account_df=account_df,
        market_data={symbol: _load_ohlcv(symbol, context["start_time"].replace("T", " ").replace("+00:00", ""), context["end_time"].replace("T", " ").replace("+00:00", "")) for symbol in stored_symbols},
    )
    equity_values = np.array([point["equity"] for point in equity_series], dtype=float)
    initial_balance = float(context["built_config"]["position"]["initial_capital"])
    if len(equity_values) > 0:
        peaks = np.maximum.accumulate(equity_values)
        drawdowns = (peaks - equity_values) / peaks
        computed_metrics = {
            "final_balance": float(equity_values[-1]),
            "peak_balance": float(np.max(equity_values)),
            "total_return_pct": float(((equity_values[-1] - initial_balance) / initial_balance) * 100),
            "max_drawdown_pct": float(np.max(drawdowns) * 100),
            "equity_points": len(equity_series),
        }
    else:
        computed_metrics = {
            "final_balance": initial_balance,
            "peak_balance": initial_balance,
            "total_return_pct": 0.0,
            "max_drawdown_pct": 0.0,
            "equity_points": 0,
        }

    payload = {
        "mode": "official_high_level_api",
        "backtest_id": context["backtest_id"],
        "name": context["name"],
        "catalog_path": str(catalog.path),
        "stored_symbols": stored_symbols,
        "run_config_id": run_config.id,
        "node_result": {
            "total_orders": results[0].total_orders,
            "total_positions": results[0].total_positions,
            "stats_pnls": results[0].stats_pnls,
            "stats_returns": results[0].stats_returns,
        },
        "metrics": computed_metrics,
    }
    OUTPUT_PATH.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    node.dispose()
    return payload


if __name__ == "__main__":
    result = run()
    print(json.dumps(result, ensure_ascii=False, indent=2))
    print(f"\nSaved BacktestNode summary to {OUTPUT_PATH}")
