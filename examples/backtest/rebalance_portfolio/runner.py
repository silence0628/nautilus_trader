from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path

import pandas as pd

from data import build_rebalance_demo_data
from instruments import btcusdt_binance
from instruments import ethusdt_binance
from instruments import solusdt_binance
from strategy import BasketAssetConfig
from strategy import RebalancePortfolioConfig
from strategy import RebalancePortfolioStrategy

from nautilus_trader.adapters.binance import BINANCE_VENUE
from nautilus_trader.backtest.engine import BacktestEngine
from nautilus_trader.config import BacktestEngineConfig
from nautilus_trader.config import LoggingConfig
from nautilus_trader.model.currencies import USDT
from nautilus_trader.model.enums import AccountType
from nautilus_trader.model.enums import OmsType
from nautilus_trader.model.identifiers import TraderId
from nautilus_trader.model.objects import Money


RESULTS_DIR = Path(__file__).resolve().parent / "results"
RESULTS_DIR.mkdir(parents=True, exist_ok=True)


@dataclass(frozen=True, slots=True)
class RebalanceRunSummary:
    trader_id: str
    rebalance_threshold: Decimal
    cash_reserve_ratio: Decimal
    min_trade_value: Decimal
    max_single_trade_ratio: Decimal
    warmup_bars: int
    min_cooldown_bars: int
    rebalance_cycles: int
    fill_count: int
    position_count: int
    starting_balance: Decimal
    ending_portfolio_value: Decimal
    total_return_pct: Decimal
    ending_usdt: Decimal
    ending_btc: Decimal
    ending_eth: Decimal
    ending_sol: Decimal


def create_rebalance_config(
    btc,
    eth,
    sol,
    bar_types: dict[str, object],
    *,
    rebalance_threshold: Decimal = Decimal("0.10"),
    cash_reserve_ratio: Decimal = Decimal("0.03"),
    min_trade_value: Decimal = Decimal("250"),
    max_single_trade_ratio: Decimal = Decimal("0.18"),
    warmup_bars: int = 24,
    min_cooldown_bars: int = 12,
) -> RebalancePortfolioConfig:
    return RebalancePortfolioConfig(
        venue="BINANCE",
        assets=(
            BasketAssetConfig(
                pair="BTCUSDT",
                instrument_id=btc.id,
                bar_type=bar_types["BTCUSDT"],
                target_weight=Decimal("0.42"),
            ),
            BasketAssetConfig(
                pair="ETHUSDT",
                instrument_id=eth.id,
                bar_type=bar_types["ETHUSDT"],
                target_weight=Decimal("0.28"),
            ),
            BasketAssetConfig(
                pair="SOLUSDT",
                instrument_id=sol.id,
                bar_type=bar_types["SOLUSDT"],
                target_weight=Decimal("0.30"),
            ),
        ),
        rebalance_threshold=rebalance_threshold,
        cash_reserve_ratio=cash_reserve_ratio,
        min_trade_value=min_trade_value,
        max_single_trade_ratio=max_single_trade_ratio,
        warmup_bars=warmup_bars,
        min_cooldown_bars=min_cooldown_bars,
    )


def _latest_balances(account_report: pd.DataFrame) -> dict[str, Decimal]:
    if account_report.empty:
        return {}

    latest_ts = account_report.index.max()
    latest_rows = account_report.loc[latest_ts]
    if isinstance(latest_rows, pd.Series):
        latest_rows = latest_rows.to_frame().T

    balances: dict[str, Decimal] = {}
    for _, row in latest_rows.iterrows():
        currency = str(row["currency"])
        balances[currency] = Decimal(str(row["total"]))
    return balances


def summarize_run(
    strategy: RebalancePortfolioStrategy,
    account_report: pd.DataFrame,
    fills_report: pd.DataFrame,
    positions_report: pd.DataFrame,
    latest_prices: dict[str, Decimal],
    trader_id: str,
) -> RebalanceRunSummary:
    balances = _latest_balances(account_report)
    starting_balance = Decimal("100000")
    ending_usdt = balances.get("USDT", Decimal("0"))
    ending_btc = balances.get("BTC", Decimal("0"))
    ending_eth = balances.get("ETH", Decimal("0"))
    ending_sol = balances.get("SOL", Decimal("0"))
    ending_portfolio_value = (
        ending_usdt
        + ending_btc * latest_prices["BTCUSDT"]
        + ending_eth * latest_prices["ETHUSDT"]
        + ending_sol * latest_prices["SOLUSDT"]
    )
    total_return_pct = ((ending_portfolio_value / starting_balance) - Decimal("1")) * Decimal("100")

    return RebalanceRunSummary(
        trader_id=trader_id,
        rebalance_threshold=strategy.config.rebalance_threshold,
        cash_reserve_ratio=strategy.config.cash_reserve_ratio,
        min_trade_value=strategy.config.min_trade_value,
        max_single_trade_ratio=strategy.config.max_single_trade_ratio,
        warmup_bars=strategy.config.warmup_bars,
        min_cooldown_bars=strategy.config.min_cooldown_bars,
        rebalance_cycles=strategy.total_rebalances,
        fill_count=len(fills_report),
        position_count=len(positions_report),
        starting_balance=starting_balance,
        ending_portfolio_value=ending_portfolio_value,
        total_return_pct=total_return_pct,
        ending_usdt=ending_usdt,
        ending_btc=ending_btc,
        ending_eth=ending_eth,
        ending_sol=ending_sol,
    )


def run_rebalance_backtest(
    *,
    trader_id: str,
    rebalance_threshold: Decimal = Decimal("0.10"),
    cash_reserve_ratio: Decimal = Decimal("0.03"),
    min_trade_value: Decimal = Decimal("250"),
    max_single_trade_ratio: Decimal = Decimal("0.18"),
    warmup_bars: int = 24,
    min_cooldown_bars: int = 12,
    log_level: str = "WARNING",
) -> tuple[RebalanceRunSummary, pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    engine = BacktestEngine(
        config=BacktestEngineConfig(
            trader_id=TraderId(trader_id),
            logging=LoggingConfig(log_level=log_level),
        )
    )

    engine.add_venue(
        venue=BINANCE_VENUE,
        oms_type=OmsType.NETTING,
        account_type=AccountType.CASH,
        base_currency=None,
        starting_balances=[Money(100_000, USDT)],
    )

    btc = btcusdt_binance()
    eth = ethusdt_binance()
    sol = solusdt_binance()
    engine.add_instrument(btc)
    engine.add_instrument(eth)
    engine.add_instrument(sol)

    prepared = build_rebalance_demo_data(btc, eth, sol)
    for bars in prepared["bars"].values():
        engine.add_data(bars)

    strategy = RebalancePortfolioStrategy(
        config=create_rebalance_config(
            btc,
            eth,
            sol,
            prepared["bar_types"],
            rebalance_threshold=rebalance_threshold,
            cash_reserve_ratio=cash_reserve_ratio,
            min_trade_value=min_trade_value,
            max_single_trade_ratio=max_single_trade_ratio,
            warmup_bars=warmup_bars,
            min_cooldown_bars=min_cooldown_bars,
        )
    )
    engine.add_strategy(strategy)
    engine.run()

    account_report = engine.trader.generate_account_report(BINANCE_VENUE)
    fills_report = engine.trader.generate_order_fills_report()
    positions_report = engine.trader.generate_positions_report()

    latest_prices = {
        pair: bars[-1].close.as_decimal()
        for pair, bars in prepared["bars"].items()
    }
    summary = summarize_run(
        strategy,
        account_report,
        fills_report,
        positions_report,
        latest_prices,
        trader_id,
    )
    engine.dispose()
    return summary, account_report, fills_report, positions_report
