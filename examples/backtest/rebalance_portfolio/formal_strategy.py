from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

import msgspec

from nautilus_trader.config import StrategyConfig
from nautilus_trader.model.data import Bar, BarType
from nautilus_trader.model.enums import OrderSide
from nautilus_trader.model.identifiers import InstrumentId
from nautilus_trader.trading.strategy import Strategy


ZERO = Decimal("0")
ONE = Decimal("1")


class ResearchAssetConfig(msgspec.Struct, frozen=True):
    pair: str
    weight: float
    instrument_id: str
    bar_type: str


@dataclass(slots=True)
class _AssetState:
    pair: str
    instrument_id: InstrumentId
    target_weight: Decimal
    base_currency: object | None = None
    latest_close: Decimal | None = None
    latest_ts: int | None = None
    abs_returns: deque[Decimal] | None = None
    close_history: deque[Decimal] | None = None


@dataclass(slots=True)
class _PlanItem:
    instrument_id: InstrumentId
    current_value: Decimal
    target_value: Decimal
    deviation: Decimal
    delta_value: Decimal


@dataclass(slots=True)
class _RebalancePlan:
    should_rebalance: bool
    items: list[_PlanItem]
    trend_blocked_count: int = 0


class ResearchParametersConfig(msgspec.Struct, frozen=True):
    assets: tuple[ResearchAssetConfig, ...]
    rebalance_threshold: float = 0.10
    min_cooldown_secs: int = 86_400
    cash_reserve_ratio: float = 0.03
    min_trade_value: float = 50.0
    max_single_trade_ratio: float = 0.20
    max_drawdown: float = 0.10
    max_daily_rebalances: int = 1
    use_volatility_filter: bool = True
    volatility_lookback_bars: int = 48
    volatility_threshold_multiplier: float = 1.5
    use_trend_filter: bool = True
    trend_lookback_bars: int = 60
    trend_strength_buffer: float = 0.02
    kill_switch_mode: str = "liquidate_and_resume"
    kill_switch_resume_secs: int = 7_862_400
    close_positions_on_stop: bool = False
    post_resume_band_secs: int = 0
    post_resume_throttle_secs: int = 0
    post_resume_max_single_trade_ratio: float | None = None

    @property
    def basket_pairs(self) -> tuple[str, ...]:
        return tuple(asset.pair for asset in self.assets)

    @property
    def target_weights(self) -> dict[str, Decimal]:
        return {
            asset.pair: Decimal(str(asset.weight))
            for asset in self.assets
        }


class ResearchRebalancingConfig(StrategyConfig, frozen=True):
    trading: dict[str, Any]
    position: dict[str, Any]
    platforms: dict[str, Any]
    parameters: ResearchParametersConfig


class FormalRebalancingStrategy(Strategy):
    """
    Research-side local equivalent of the formal stone rebalancing strategy.

    This version intentionally keeps the same core portfolio logic, kill switch,
    filters, and indicator output shape so research can happen in Nautilus first.
    """

    def __init__(self, config: ResearchRebalancingConfig) -> None:
        super().__init__(config)
        self.asset_states: dict[InstrumentId, _AssetState] = {}
        self.bar_types: dict[InstrumentId, BarType] = {}
        self.quote_currency = None
        self.peak_portfolio_value = ZERO
        self.last_portfolio_value = ZERO
        self.last_rebalance_ts = 0
        self.last_sync_ts = 0
        self.daily_rebalance_count = 0
        self.daily_rebalance_date = None
        self.kill_switch_triggered = False
        self.kill_switch_triggered_ts: int | None = None
        self.kill_switch_resume_ts: int | None = None
        self.last_resume_ts: int | None = None
        self.total_rebalances = 0
        self.volatility_pause_count = 0
        self.trend_pause_count = 0
        self.order_denied_count = 0
        self.order_rejected_count = 0
        self.max_observed_drawdown = ZERO
        self._indicator_history: dict[str, dict[str, Any]] = {}
        self._latest_plan_summary: list[dict[str, Any]] = []

    def on_start(self) -> None:
        total_weight = sum(self.config.parameters.target_weights.values(), ZERO)
        if total_weight <= ZERO or total_weight > ONE:
            self.log.error("Target weights must sum to a positive value no greater than 1.0")
            self.stop()
            return

        if self.config.parameters.kill_switch_mode not in {
            "liquidate_and_stop",
            "liquidate_and_resume",
            "pause_rebalance_and_resume",
        }:
            self.log.error("Unsupported kill_switch_mode for rebalancing strategy")
            self.stop()
            return

        for asset in self.config.parameters.assets:
            instrument_id = InstrumentId.from_str(asset.instrument_id)
            instrument = self.cache.instrument(instrument_id)
            if instrument is None:
                self.log.error(f"Instrument not found for {asset.instrument_id}")
                self.stop()
                return

            if self.quote_currency is None:
                self.quote_currency = instrument.quote_currency
            elif instrument.quote_currency != self.quote_currency:
                self.log.error("All configured instruments must share the same quote currency")
                self.stop()
                return

            bar_type = BarType.from_str(asset.bar_type)
            self.asset_states[instrument_id] = _AssetState(
                pair=asset.pair,
                instrument_id=instrument_id,
                target_weight=self.config.parameters.target_weights[asset.pair],
                base_currency=instrument.base_currency,
                abs_returns=deque(maxlen=self.config.parameters.volatility_lookback_bars),
                close_history=deque(maxlen=self.config.parameters.trend_lookback_bars),
            )
            self.bar_types[instrument_id] = bar_type
            self.subscribe_bars(bar_type)

        self._indicator_history = {
            "portfolio_value": {"type": "PORTFOLIO_VALUE", "config": {}, "points": []},
            "drawdown": {"type": "DRAWDOWN", "config": {}, "points": []},
            "rebalance_count": {"type": "REBALANCE_COUNT", "config": {}, "points": []},
            "kill_switch": {"type": "KILL_SWITCH", "config": {}, "points": []},
        }
        self.log.info(
            "Rebalance strategy started: "
            f"pairs={', '.join(asset.pair for asset in self.config.parameters.assets)}, "
            f"threshold={self.config.parameters.rebalance_threshold * 100:.2f}%, "
            f"cooldown={self.config.parameters.min_cooldown_secs}s, "
            f"reserve={self.config.parameters.cash_reserve_ratio * 100:.2f}%, "
            f"kill_switch={self.config.parameters.kill_switch_mode}",
        )

    def on_stop(self) -> None:
        for bar_type in self.bar_types.values():
            self.unsubscribe_bars(bar_type)
        if self.config.parameters.close_positions_on_stop:
            for instrument_id in self.asset_states:
                self.close_all_positions(instrument_id)

    def on_bar(self, bar: Bar) -> None:
        state = self.asset_states.get(bar.bar_type.instrument_id)
        if state is None:
            return

        close_price = bar.close.as_decimal()
        if close_price <= ZERO:
            return

        if state.latest_close and state.latest_close > ZERO and state.abs_returns is not None:
            abs_return = abs((close_price - state.latest_close) / state.latest_close)
            state.abs_returns.append(abs_return)

        state.latest_close = close_price
        state.latest_ts = bar.ts_event
        if state.close_history is not None:
            state.close_history.append(close_price)

        if not self._all_assets_synchronized(bar.ts_event):
            return
        if bar.ts_event == self.last_sync_ts:
            return
        self.last_sync_ts = bar.ts_event

        portfolio_value = self._portfolio_value()
        if portfolio_value <= ZERO:
            return

        current_dt = datetime.fromtimestamp(bar.ts_event / 1_000_000_000, tz=UTC)
        self._reset_daily_limit_if_needed(current_dt)

        resumed_this_bar = False
        if self.kill_switch_triggered:
            resume_ts = self.kill_switch_resume_ts
            if (
                self.config.parameters.kill_switch_mode == "liquidate_and_resume"
                and resume_ts is not None
                and bar.ts_event >= resume_ts
            ):
                self.kill_switch_triggered = False
                self.kill_switch_resume_ts = None
                self.last_resume_ts = bar.ts_event
                self.peak_portfolio_value = portfolio_value
                resumed_this_bar = True
            else:
                self._record_indicator_snapshot(bar.ts_event, portfolio_value, ZERO)
                return

        if portfolio_value > self.peak_portfolio_value:
            self.peak_portfolio_value = portfolio_value

        drawdown = ZERO
        if self.peak_portfolio_value > ZERO:
            drawdown = (self.peak_portfolio_value - portfolio_value) / self.peak_portfolio_value
        if drawdown > self.max_observed_drawdown:
            self.max_observed_drawdown = drawdown

        if (
            not self.kill_switch_triggered
            and drawdown >= Decimal(str(self.config.parameters.max_drawdown))
        ):
            self._trigger_kill_switch(drawdown)
            self._record_indicator_snapshot(bar.ts_event, portfolio_value, drawdown)
            return

        if self._is_high_volatility():
            self.volatility_pause_count += 1
            self._record_indicator_snapshot(bar.ts_event, portfolio_value, drawdown)
            return

        if not self._cooldown_passed(bar.ts_event):
            self._record_indicator_snapshot(bar.ts_event, portfolio_value, drawdown)
            return

        if self.daily_rebalance_count >= self.config.parameters.max_daily_rebalances:
            self._record_indicator_snapshot(bar.ts_event, portfolio_value, drawdown)
            return

        plan = self._build_rebalance_plan(portfolio_value)
        self._record_indicator_snapshot(bar.ts_event, portfolio_value, drawdown)
        if not plan.should_rebalance or not plan.items:
            return
        if self.kill_switch_triggered and not resumed_this_bar:
            return

        self._execute_rebalance(plan, portfolio_value, current_dt)

    def on_order_rejected(self, event) -> None:
        self.order_rejected_count += 1
        self.log.warning(f"Rebalancing order rejected: {event}")

    def on_order_denied(self, event) -> None:
        self.order_denied_count += 1
        self.log.warning(f"Rebalancing order denied: {event}")

    def get_indicator_history(self) -> dict[str, Any]:
        return self._indicator_history

    def get_runtime_summary(self) -> dict[str, Any]:
        latest_ts = max(
            (state.latest_ts for state in self.asset_states.values() if state.latest_ts is not None),
            default=None,
        )
        return {
            "portfolio_value": float(self.last_portfolio_value) if self.last_portfolio_value > ZERO else 0.0,
            "peak_portfolio_value": float(self.peak_portfolio_value) if self.peak_portfolio_value > ZERO else 0.0,
            "max_observed_drawdown_pct": float(self.max_observed_drawdown * Decimal("100")),
            "kill_switch_triggered": self.kill_switch_triggered,
            "total_rebalances": self.total_rebalances,
            "daily_rebalance_count": self.daily_rebalance_count,
            "volatility_pause_count": self.volatility_pause_count,
            "trend_pause_count": self.trend_pause_count,
            "order_denied_count": self.order_denied_count,
            "order_rejected_count": self.order_rejected_count,
            "latest_bar_time_utc": (
                datetime.fromtimestamp(latest_ts / 1_000_000_000, tz=UTC).isoformat()
                if latest_ts is not None
                else None
            ),
            "plan_items": self._latest_plan_summary,
        }

    def _all_assets_synchronized(self, ts_event: int) -> bool:
        return all(state.latest_ts == ts_event for state in self.asset_states.values())

    def _cooldown_passed(self, ts_event: int) -> bool:
        if self.last_rebalance_ts == 0:
            return True
        elapsed_secs = (ts_event - self.last_rebalance_ts) / 1_000_000_000
        return elapsed_secs >= self.config.parameters.min_cooldown_secs

    def _reset_daily_limit_if_needed(self, current_dt: datetime) -> None:
        current_date = current_dt.date()
        if self.daily_rebalance_date != current_date:
            self.daily_rebalance_date = current_date
            self.daily_rebalance_count = 0

    def _strategy_venue(self):
        first_instrument_id = next(iter(self.asset_states))
        return first_instrument_id.venue

    def _portfolio_value(self) -> Decimal:
        venue = self._strategy_venue()
        account = self.portfolio.account(venue)
        if account is None:
            return ZERO

        total_value = ZERO
        if self.quote_currency is not None:
            quote_balance = account.balance_total(self.quote_currency)
            if quote_balance is not None:
                total_value += quote_balance.as_decimal()

        for state in self.asset_states.values():
            if state.base_currency is None or state.latest_close is None:
                continue
            balance = account.balance_total(state.base_currency)
            if balance is None:
                continue
            total_value += balance.as_decimal() * state.latest_close

        self.last_portfolio_value = total_value
        return total_value

    def _current_asset_value(self, instrument_id: InstrumentId) -> Decimal:
        state = self.asset_states[instrument_id]
        venue = self._strategy_venue()
        account = self.portfolio.account(venue)
        if account is None or state.base_currency is None or state.latest_close is None:
            return ZERO
        balance = account.balance_total(state.base_currency)
        if balance is None:
            return ZERO
        return balance.as_decimal() * state.latest_close

    def _asset_balance(self, instrument_id: InstrumentId) -> Decimal:
        state = self.asset_states[instrument_id]
        venue = self._strategy_venue()
        account = self.portfolio.account(venue)
        if account is None or state.base_currency is None:
            return ZERO
        balance = account.balance_total(state.base_currency)
        return ZERO if balance is None else balance.as_decimal()

    def _free_quote_cash(self) -> Decimal:
        venue = self._strategy_venue()
        account = self.portfolio.account(venue)
        if account is None or self.quote_currency is None:
            return ZERO
        quote_balance = account.balance_free(self.quote_currency)
        return ZERO if quote_balance is None else quote_balance.as_decimal()

    def _build_rebalance_plan(self, portfolio_value: Decimal) -> _RebalancePlan:
        total_target_weight = sum((state.target_weight for state in self.asset_states.values()), ZERO)
        threshold = self._active_rebalance_threshold()
        min_trade_value = Decimal(str(self.config.parameters.min_trade_value))
        max_trade_value = portfolio_value * self._active_max_single_trade_ratio()

        plan_items: list[_PlanItem] = []
        should_rebalance = False
        trend_blocked_count = 0

        for instrument_id, state in self.asset_states.items():
            current_value = self._current_asset_value(instrument_id)
            target_value = self._target_notional(state, portfolio_value, total_target_weight)
            deviation = (current_value - target_value) / portfolio_value if portfolio_value > ZERO else ZERO

            if abs(deviation) > threshold:
                should_rebalance = True
                delta_value = self._rebalance_delta_value(
                    current_value=current_value,
                    target_value=target_value,
                    deviation=deviation,
                    portfolio_value=portfolio_value,
                    threshold=threshold,
                )
            else:
                delta_value = ZERO

            capped_delta_value = max(-max_trade_value, min(delta_value, max_trade_value))
            if abs(capped_delta_value) < min_trade_value:
                continue
            if self._blocked_by_trend_filter(state, capped_delta_value):
                trend_blocked_count += 1
                continue

            plan_items.append(
                _PlanItem(
                    instrument_id=instrument_id,
                    current_value=current_value,
                    target_value=target_value,
                    deviation=deviation,
                    delta_value=capped_delta_value,
                )
            )

        plan = _RebalancePlan(
            should_rebalance=should_rebalance,
            items=plan_items,
            trend_blocked_count=trend_blocked_count,
        )
        self._latest_plan_summary = [
            {
                "pair": self.asset_states[item.instrument_id].pair,
                "current_value": float(item.current_value),
                "target_value": float(item.target_value),
                "deviation_pct": float(item.deviation * Decimal("100")),
                "delta_value": float(item.delta_value),
            }
            for item in plan.items
        ]
        return plan

    def _target_notional(
        self,
        state: _AssetState,
        portfolio_value: Decimal,
        total_target_weight: Decimal,
    ) -> Decimal:
        investable_value = portfolio_value * (ONE - Decimal(str(self.config.parameters.cash_reserve_ratio)))
        normalized_weight = state.target_weight / total_target_weight
        return investable_value * normalized_weight

    def _rebalance_delta_value(
        self,
        *,
        current_value: Decimal,
        target_value: Decimal,
        deviation: Decimal,
        portfolio_value: Decimal,
        threshold: Decimal,
    ) -> Decimal:
        if not self._use_rebalance_band_mode():
            return target_value - current_value
        if portfolio_value <= ZERO or deviation == ZERO:
            return ZERO
        boundary_deviation = threshold if deviation > ZERO else -threshold
        boundary_value = target_value + (boundary_deviation * portfolio_value)
        return boundary_value - current_value

    def _use_rebalance_band_mode(self) -> bool:
        if self.config.parameters.post_resume_band_secs <= 0:
            return False
        if self.last_resume_ts is None or self.last_sync_ts == 0:
            return False
        elapsed_secs = (self.last_sync_ts - self.last_resume_ts) / 1_000_000_000
        return 0 <= elapsed_secs <= self.config.parameters.post_resume_band_secs

    def _is_post_resume_throttle_active(self) -> bool:
        if self.config.parameters.post_resume_throttle_secs <= 0:
            return False
        if self.last_resume_ts is None or self.last_sync_ts == 0:
            return False
        elapsed_secs = (self.last_sync_ts - self.last_resume_ts) / 1_000_000_000
        return 0 <= elapsed_secs <= self.config.parameters.post_resume_throttle_secs

    def _active_rebalance_threshold(self) -> Decimal:
        return Decimal(str(self.config.parameters.rebalance_threshold))

    def _active_max_single_trade_ratio(self) -> Decimal:
        throttled_ratio = self.config.parameters.post_resume_max_single_trade_ratio
        if self._is_post_resume_throttle_active() and throttled_ratio is not None:
            return Decimal(str(throttled_ratio))
        return Decimal(str(self.config.parameters.max_single_trade_ratio))

    def _blocked_by_trend_filter(self, state: _AssetState, delta_value: Decimal) -> bool:
        if not self.config.parameters.use_trend_filter:
            return False
        history = state.close_history
        if history is None or len(history) < self.config.parameters.trend_lookback_bars:
            return False

        current_close = history[-1]
        baseline = sum(history, ZERO) / Decimal(len(history))
        if baseline <= ZERO:
            return False

        buffer = Decimal(str(self.config.parameters.trend_strength_buffer))
        if delta_value < ZERO and current_close >= baseline * (ONE + buffer):
            return True
        if delta_value > ZERO and current_close <= baseline * (ONE - buffer):
            return True
        return False

    def _is_high_volatility(self) -> bool:
        if not self.config.parameters.use_volatility_filter:
            return False
        multiplier = Decimal(str(self.config.parameters.volatility_threshold_multiplier))
        required = self.config.parameters.volatility_lookback_bars
        for state in self.asset_states.values():
            returns = state.abs_returns
            if returns is None or len(returns) < required:
                continue
            current_abs_return = returns[-1]
            avg_abs_return = sum(returns, ZERO) / Decimal(len(returns))
            if avg_abs_return > ZERO and current_abs_return > avg_abs_return * multiplier:
                return True
        return False

    def _execute_rebalance(
        self,
        plan: _RebalancePlan,
        portfolio_value: Decimal,
        current_dt: datetime,
    ) -> None:
        sells = [item for item in plan.items if item.delta_value < ZERO]
        buys = [item for item in plan.items if item.delta_value > ZERO]

        estimated_cash = self._free_quote_cash() + sum(abs(item.delta_value) for item in sells)
        sell_failed = False

        if plan.items:
            plan_desc = "; ".join(
                f"{self.asset_states[item.instrument_id].pair}: "
                f"dev={item.deviation * Decimal('100'):.2f}%, "
                f"target={item.target_value:.2f}, current={item.current_value:.2f}, "
                f"delta={item.delta_value:.2f}"
                for item in plan.items
            )
            self.log.info(
                "Rebalance plan @ "
                f"{current_dt.isoformat()} UTC | portfolio={float(portfolio_value):.2f} | "
                f"free_cash={float(self._free_quote_cash()):.2f} | items={plan_desc}",
            )

        for item in sorted(sells, key=lambda x: x.delta_value):
            submitted = self._submit_rebalance_order(item, OrderSide.SELL)
            if not submitted:
                sell_failed = True
                break

        if not sell_failed:
            for item in sorted(buys, key=lambda x: x.delta_value, reverse=True):
                if item.delta_value > estimated_cash:
                    continue
                submitted = self._submit_rebalance_order(item, OrderSide.BUY)
                if submitted:
                    estimated_cash -= item.delta_value

        self.last_rebalance_ts = int(current_dt.timestamp() * 1_000_000_000)
        self.daily_rebalance_count += 1
        self.total_rebalances += 1

    def _submit_rebalance_order(self, item: _PlanItem, side: OrderSide) -> bool:
        state = self.asset_states[item.instrument_id]
        instrument = self.cache.instrument(item.instrument_id)
        if instrument is None or state.latest_close is None or state.latest_close <= ZERO:
            return False

        target_notional = abs(item.delta_value)
        raw_qty = target_notional / state.latest_close
        try:
            quantity = instrument.make_qty(raw_qty, round_down=True)
        except ValueError:
            return False
        if quantity.as_decimal() <= ZERO:
            return False

        if side == OrderSide.SELL:
            available = self._asset_balance(item.instrument_id)
            sell_qty = min(quantity.as_decimal(), available)
            try:
                quantity = instrument.make_qty(sell_qty, round_down=True)
            except ValueError:
                return False
            if quantity.as_decimal() <= ZERO:
                return False

        self.log.info(
            "Submit "
            f"{side.name} order | pair={state.pair} | est_price={float(state.latest_close):.8f} | "
            f"qty={float(quantity.as_decimal()):.8f} | notional={float(abs(item.delta_value)):.2f} | "
            f"deviation={float(item.deviation * Decimal('100')):.2f}% | "
            f"target_weight={float(state.target_weight * Decimal('100')):.2f}%",
        )
        order = self.order_factory.market(
            instrument_id=item.instrument_id,
            order_side=side,
            quantity=quantity,
        )
        self.submit_order(order)
        return True

    def _trigger_kill_switch(self, drawdown: Decimal) -> None:
        if self.kill_switch_triggered:
            return
        self.kill_switch_triggered = True
        self.kill_switch_triggered_ts = self.last_sync_ts
        self.log.warning(f"Kill switch triggered at drawdown={drawdown:.2%}")

        if self.config.parameters.kill_switch_mode != "pause_rebalance_and_resume":
            for instrument_id in self.asset_states:
                self.close_all_positions(instrument_id)

        if self.config.parameters.kill_switch_mode in {
            "liquidate_and_resume",
            "pause_rebalance_and_resume",
        }:
            self.kill_switch_resume_ts = self.last_sync_ts + int(
                self.config.parameters.kill_switch_resume_secs * 1_000_000_000
            )
            return
        self.stop()

    def _record_indicator_snapshot(self, ts_event: int, portfolio_value: Decimal, drawdown: Decimal) -> None:
        ts_seconds = ts_event // 1_000_000_000
        self._indicator_history["portfolio_value"]["points"].append(
            {"time": ts_seconds, "value": float(portfolio_value)}
        )
        self._indicator_history["drawdown"]["points"].append(
            {"time": ts_seconds, "value": float(drawdown)}
        )
        self._indicator_history["rebalance_count"]["points"].append(
            {"time": ts_seconds, "value": self.total_rebalances}
        )
        self._indicator_history["kill_switch"]["points"].append(
            {"time": ts_seconds, "value": 1 if self.kill_switch_triggered else 0}
        )
