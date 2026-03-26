from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC
from datetime import datetime
from datetime import timedelta
from decimal import Decimal

from nautilus_trader.core.datetime import dt_to_unix_nanos
from nautilus_trader.model.data import Bar
from nautilus_trader.model.data import BarSpecification
from nautilus_trader.model.data import BarType
from nautilus_trader.model.enums import BarAggregation
from nautilus_trader.model.enums import PriceType
from nautilus_trader.model.instruments.base import Instrument
from nautilus_trader.model.objects import Quantity


@dataclass(frozen=True, slots=True)
class SyntheticAssetSeriesConfig:
    pair: str
    start_price: Decimal
    drift_per_bar: Decimal
    cycle_amplitude: Decimal
    cycle_length: int
    shock_every: int = 0
    shock_size: Decimal = Decimal("0")


def make_hourly_bar_type(instrument: Instrument) -> BarType:
    return BarType(
        instrument_id=instrument.id,
        bar_spec=BarSpecification(
            step=1,
            aggregation=BarAggregation.HOUR,
            price_type=PriceType.LAST,
        ),
    )


def generate_hourly_bars(
    instrument: Instrument,
    bar_type: BarType,
    config: SyntheticAssetSeriesConfig,
    periods: int,
    start_dt: datetime,
) -> list[Bar]:
    bars: list[Bar] = []
    price = config.start_price

    for index in range(periods):
        cycle_position = (index % config.cycle_length) / Decimal(str(config.cycle_length))
        cycle_bias = (cycle_position - Decimal("0.5")) * config.cycle_amplitude
        shock = Decimal("0")
        if config.shock_every and index and index % config.shock_every == 0:
            shock = config.shock_size

        open_price = price
        close_price = max(
            Decimal("0.01"),
            price + config.drift_per_bar + cycle_bias + shock,
        )
        high_price = max(open_price, close_price) * Decimal("1.01")
        low_price = min(open_price, close_price) * Decimal("0.99")
        ts_event = dt_to_unix_nanos(start_dt + timedelta(hours=index))

        bars.append(
            Bar(
                bar_type=bar_type,
                open=instrument.make_price(open_price),
                high=instrument.make_price(high_price),
                low=instrument.make_price(low_price),
                close=instrument.make_price(close_price),
                volume=instrument.make_qty(Decimal("100") + Decimal(index % 50)),
                ts_event=ts_event,
                ts_init=ts_event,
            )
        )
        price = close_price

    return bars


def build_rebalance_demo_data(
    btc_instrument: Instrument,
    eth_instrument: Instrument,
    sol_instrument: Instrument,
    periods: int = 24 * 120,
) -> dict[str, object]:
    start_dt = datetime(2024, 1, 1, tzinfo=UTC)

    btc_bar_type = make_hourly_bar_type(btc_instrument)
    eth_bar_type = make_hourly_bar_type(eth_instrument)
    sol_bar_type = make_hourly_bar_type(sol_instrument)

    btc_bars = generate_hourly_bars(
        instrument=btc_instrument,
        bar_type=btc_bar_type,
        config=SyntheticAssetSeriesConfig(
            pair="BTCUSDT",
            start_price=Decimal("42000"),
            drift_per_bar=Decimal("8"),
            cycle_amplitude=Decimal("180"),
            cycle_length=48,
            shock_every=24 * 20,
            shock_size=Decimal("-1200"),
        ),
        periods=periods,
        start_dt=start_dt,
    )
    eth_bars = generate_hourly_bars(
        instrument=eth_instrument,
        bar_type=eth_bar_type,
        config=SyntheticAssetSeriesConfig(
            pair="ETHUSDT",
            start_price=Decimal("2200"),
            drift_per_bar=Decimal("0.9"),
            cycle_amplitude=Decimal("45"),
            cycle_length=36,
            shock_every=24 * 18,
            shock_size=Decimal("-140"),
        ),
        periods=periods,
        start_dt=start_dt,
    )
    sol_bars = generate_hourly_bars(
        instrument=sol_instrument,
        bar_type=sol_bar_type,
        config=SyntheticAssetSeriesConfig(
            pair="SOLUSDT",
            start_price=Decimal("95"),
            drift_per_bar=Decimal("0.08"),
            cycle_amplitude=Decimal("4.5"),
            cycle_length=24,
            shock_every=24 * 15,
            shock_size=Decimal("-12"),
        ),
        periods=periods,
        start_dt=start_dt,
    )

    return {
        "start_dt": start_dt,
        "periods": periods,
        "bar_types": {
            "BTCUSDT": btc_bar_type,
            "ETHUSDT": eth_bar_type,
            "SOLUSDT": sol_bar_type,
        },
        "bars": {
            "BTCUSDT": btc_bars,
            "ETHUSDT": eth_bars,
            "SOLUSDT": sol_bars,
        },
    }
