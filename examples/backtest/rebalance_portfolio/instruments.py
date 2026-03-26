from __future__ import annotations

from decimal import Decimal

from nautilus_trader.model.currencies import SOL
from nautilus_trader.model.currencies import USDT
from nautilus_trader.model.identifiers import InstrumentId
from nautilus_trader.model.identifiers import Symbol
from nautilus_trader.model.identifiers import Venue
from nautilus_trader.model.instruments import CurrencyPair
from nautilus_trader.model.objects import Money
from nautilus_trader.model.objects import Price
from nautilus_trader.model.objects import Quantity
from nautilus_trader.test_kit.providers import TestInstrumentProvider


def btcusdt_binance() -> CurrencyPair:
    return TestInstrumentProvider.btcusdt_binance()


def ethusdt_binance() -> CurrencyPair:
    return TestInstrumentProvider.ethusdt_binance()


def solusdt_binance() -> CurrencyPair:
    return CurrencyPair(
        instrument_id=InstrumentId(symbol=Symbol("SOLUSDT"), venue=Venue("BINANCE")),
        raw_symbol=Symbol("SOLUSDT"),
        base_currency=SOL,
        quote_currency=USDT,
        price_precision=3,
        size_precision=2,
        price_increment=Price(0.001, precision=3),
        size_increment=Quantity(0.01, precision=2),
        lot_size=Quantity(0.01, precision=2),
        max_quantity=Quantity(900_000, precision=2),
        min_quantity=Quantity(0.01, precision=2),
        max_notional=None,
        min_notional=Money(5.0, USDT),
        max_price=Price(1_000_000, precision=3),
        min_price=Price(0.001, precision=3),
        margin_init=Decimal("0"),
        margin_maint=Decimal("0"),
        maker_fee=Decimal("0.001"),
        taker_fee=Decimal("0.001"),
        ts_event=0,
        ts_init=0,
    )
