from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from nautilus_trader.common.enums import LogColor
from nautilus_trader.config import StrategyConfig
from nautilus_trader.model.currencies import Currency
from nautilus_trader.model.data import Bar
from nautilus_trader.model.data import BarType
from nautilus_trader.model.enums import OrderSide
from nautilus_trader.model.enums import TimeInForce
from nautilus_trader.model.identifiers import InstrumentId
from nautilus_trader.model.instruments.base import Instrument
from nautilus_trader.model.objects import Money
from nautilus_trader.trading.strategy import Strategy


ZERO = Decimal("0")
ONE = Decimal("1")


@dataclass(frozen=True, slots=True)
class BasketAssetConfig:
    pair: str
    instrument_id: InstrumentId
    bar_type: BarType
    target_weight: Decimal


@dataclass(slots=True)
class _AssetState:
    config: BasketAssetConfig
    instrument: Instrument
    latest_close: Decimal | None = None
    latest_ts: int | None = None
    bars_seen: int = 0


class RebalancePortfolioConfig(StrategyConfig, frozen=True):
    venue: str
    assets: tuple[BasketAssetConfig, ...]
    quote_currency: str = "USDT"
    rebalance_threshold: Decimal = Decimal("0.10")
    cash_reserve_ratio: Decimal = Decimal("0.03")
    min_trade_value: Decimal = Decimal("50")
    max_single_trade_ratio: Decimal = Decimal("0.20")
    warmup_bars: int = 24
    min_cooldown_bars: int = 24
    log_rebalance_details: bool = True


class RebalancePortfolioStrategy(Strategy):
    def __init__(self, config: RebalancePortfolioConfig) -> None:
        super().__init__(config)
        self.asset_states: dict[InstrumentId, _AssetState] = {}
        self.quote_currency = Currency.from_str(config.quote_currency)
        self.last_rebalance_bar_index = -10_000
        self.total_rebalances = 0
        self.bar_index = -1

    def on_start(self) -> None:
        total_weight = sum(asset.target_weight for asset in self.config.assets)
        if total_weight <= ZERO or total_weight > ONE:
            self.log.error("Target weights must sum to a positive value no greater than 1.0")
            self.stop()
            return

        for asset in self.config.assets:
            instrument = self.cache.instrument(asset.instrument_id)
            if instrument is None:
                self.log.error(f"Could not find instrument for {asset.pair}")
                self.stop()
                return

            self.asset_states[asset.instrument_id] = _AssetState(
                config=asset,
                instrument=instrument,
            )
            self.subscribe_bars(asset.bar_type)

    def on_stop(self) -> None:
        for asset in self.config.assets:
            self.unsubscribe_bars(asset.bar_type)

    def on_bar(self, bar: Bar) -> None:
        state = self.asset_states.get(bar.bar_type.instrument_id)
        if state is None:
            return

        state.latest_close = bar.close.as_decimal()
        state.latest_ts = bar.ts_event
        state.bars_seen += 1

        if not self._is_sync_point(bar.ts_event):
            return

        self.bar_index += 1
        if min(asset.bars_seen for asset in self.asset_states.values()) < self.config.warmup_bars:
            return
        if self.bar_index - self.last_rebalance_bar_index < self.config.min_cooldown_bars:
            return

        self._rebalance_if_needed(ts_event=bar.ts_event)

    def _is_sync_point(self, ts_event: int) -> bool:
        if len(self.asset_states) != len(self.config.assets):
            return False
        return all(
            state.latest_ts == ts_event and state.latest_close is not None
            for state in self.asset_states.values()
        )

    def _rebalance_if_needed(self, ts_event: int) -> None:
        portfolio_value, cash_balance, current_values = self._portfolio_snapshot()
        if portfolio_value <= ZERO:
            return

        deployable_value = portfolio_value * (ONE - self.config.cash_reserve_ratio)
        deviations: dict[InstrumentId, Decimal] = {}
        planned_values: dict[InstrumentId, Decimal] = {}
        should_rebalance = False

        for instrument_id, state in self.asset_states.items():
            target_value = deployable_value * state.config.target_weight
            planned_values[instrument_id] = target_value
            current_value = current_values[instrument_id]
            deviation = abs(current_value - target_value) / deployable_value if deployable_value > ZERO else ZERO
            deviations[instrument_id] = deviation
            if deviation >= self.config.rebalance_threshold:
                should_rebalance = True

        if not should_rebalance:
            return

        reserve_target = portfolio_value * self.config.cash_reserve_ratio
        available_cash_to_spend = max(ZERO, cash_balance - reserve_target)

        for instrument_id, state in self.asset_states.items():
            current_value = current_values[instrument_id]
            target_value = planned_values[instrument_id]
            delta_value = target_value - current_value

            if abs(delta_value) < self.config.min_trade_value:
                continue

            max_trade_value = portfolio_value * self.config.max_single_trade_ratio
            trade_value = min(abs(delta_value), max_trade_value)
            if trade_value < self.config.min_trade_value:
                continue

            price = state.latest_close
            if price is None or price <= ZERO:
                continue

            quantity_decimal = trade_value / price
            if quantity_decimal <= ZERO:
                continue

            side = OrderSide.BUY if delta_value > ZERO else OrderSide.SELL
            if side == OrderSide.BUY:
                if available_cash_to_spend < trade_value:
                    continue
                available_cash_to_spend -= trade_value
            else:
                current_qty = self._current_asset_quantity(state.instrument)
                if current_qty <= ZERO:
                    continue
                quantity_decimal = min(quantity_decimal, current_qty)

            quantity = state.instrument.make_qty(quantity_decimal)
            if quantity.as_decimal() <= ZERO:
                continue

            order = self.order_factory.market(
                instrument_id=state.instrument.id,
                order_side=side,
                quantity=quantity,
                time_in_force=TimeInForce.IOC,
            )
            self.submit_order(order)

            if self.config.log_rebalance_details:
                self.log.info(
                    (
                        f"Rebalance {side.name} {state.config.pair}: "
                        f"current={current_value:.2f}, target={target_value:.2f}, "
                        f"trade_value={trade_value:.2f}, deviation={deviations[instrument_id]:.4f}"
                    ),
                    color=LogColor.GREEN,
                )

        self.last_rebalance_bar_index = self.bar_index
        self.total_rebalances += 1
        self.log.info(
            (
                f"Rebalance cycle completed at ts={ts_event}, "
                f"portfolio_value={portfolio_value:.2f}, cash={cash_balance:.2f}, "
                f"total_rebalances={self.total_rebalances}"
            ),
            color=LogColor.BLUE,
        )

    def _portfolio_snapshot(self) -> tuple[Decimal, Decimal, dict[InstrumentId, Decimal]]:
        account = self.portfolio.account(venue=self.config.assets[0].instrument_id.venue)
        balances_total = account.balances_total() if account is not None else {}
        cash_balance = balances_total.get(self.quote_currency, Money(0, self.quote_currency)).as_decimal()
        current_values: dict[InstrumentId, Decimal] = {}
        total_value = cash_balance

        for instrument_id, state in self.asset_states.items():
            quantity = self._current_asset_quantity(state.instrument, balances_total)
            price = state.latest_close or ZERO
            current_value = quantity * price
            current_values[instrument_id] = current_value
            total_value += current_value

        return total_value, cash_balance, current_values

    def _current_asset_quantity(
        self,
        instrument: Instrument,
        balances_total: dict | None = None,
    ) -> Decimal:
        if balances_total is None:
            account = self.portfolio.account(venue=instrument.venue)
            balances_total = account.balances_total() if account is not None else {}
        base_balance = balances_total.get(instrument.base_currency)
        if base_balance is not None:
            return base_balance.as_decimal()
        net_position = self.portfolio.net_position(instrument.id)
        if net_position is None:
            return ZERO
        if hasattr(net_position, "as_decimal"):
            return net_position.as_decimal()
        return Decimal(str(net_position))
