from __future__ import annotations

import os
from pathlib import Path

from nautilus_trader.adapters.binance import BinanceAccountType
from nautilus_trader.adapters.binance import BinanceDataClientConfig
from nautilus_trader.adapters.binance import BinanceExecClientConfig
from nautilus_trader.adapters.binance import BinanceKeyType
from nautilus_trader.adapters.binance import BinanceLiveDataClientFactory
from nautilus_trader.adapters.binance.common.urls import get_ws_base_url
from nautilus_trader.adapters.binance.factories import get_cached_binance_http_client
from nautilus_trader.adapters.binance.factories import get_cached_binance_spot_instrument_provider
from nautilus_trader.adapters.binance.spot.execution import BinanceSpotExecutionClient
from nautilus_trader.config import InstrumentProviderConfig
from nautilus_trader.config import LiveExecEngineConfig
from nautilus_trader.config import LoggingConfig
from nautilus_trader.config import TradingNodeConfig
from nautilus_trader.live.factories import LiveExecClientFactory
from nautilus_trader.live.node import TradingNode
from nautilus_trader.model.identifiers import InstrumentId
from nautilus_trader.model.identifiers import TraderId

from configs.loader import build_research_context
from strategy_core.rebalance_strategy import FormalRebalancingStrategy
from strategy_core.rebalance_strategy import ResearchRebalancingConfig


REQUIRED_TESTNET_ENV_VARS = (
    "BINANCE_TESTNET_API_KEY",
)
OPTIONAL_TESTNET_SECRET_FILE_ENV = "BINANCE_TESTNET_API_SECRET_FILE"


class BinanceSpotTestnetCompatExecutionClient(BinanceSpotExecutionClient):
    async def _connect(self) -> None:
        await self._instrument_provider.initialize()
        await self._update_account_state()
        await self._await_account_registered()
        await self._init_dual_side_position()

        server_time: int = await self._spot_http_market.request_server_time()
        self._log.info(f"Binance server time {server_time} UNIX (ms)")

        nautilus_time: int = self._clock.timestamp_ms()
        self._log.info(f"Nautilus clock time {nautilus_time} UNIX (ms)")

        # Spot testnet currently returns 410 Gone on legacy listenKey bootstrap.
        # Keep execution available via REST + reconciliation without user stream init.
        self._listen_key = None
        self._log.warning(
            "Skipping Spot testnet listenKey bootstrap; running in compatibility mode",
        )


class BinanceSpotTestnetCompatExecClientFactory(LiveExecClientFactory):
    @staticmethod
    def create(  # type: ignore
        loop,
        name,
        config,
        msgbus,
        cache,
        clock,
    ) -> BinanceSpotTestnetCompatExecutionClient:
        client = get_cached_binance_http_client(
            clock=clock,
            account_type=config.account_type,
            api_key=config.api_key,
            api_secret=config.api_secret,
            key_type=config.key_type,
            base_url=config.base_url_http,
            is_testnet=config.testnet,
            is_us=config.us,
            proxy_url=config.proxy_url,
        )
        provider = get_cached_binance_spot_instrument_provider(
            client=client,
            clock=clock,
            account_type=config.account_type,
            is_testnet=config.testnet,
            config=config.instrument_provider,
            venue=config.venue,
        )
        return BinanceSpotTestnetCompatExecutionClient(
            loop=loop,
            client=client,
            msgbus=msgbus,
            cache=cache,
            clock=clock,
            instrument_provider=provider,
            account_type=config.account_type,
            base_url_ws=config.base_url_ws or get_ws_base_url(
                account_type=config.account_type,
                is_testnet=config.testnet,
                is_us=config.us,
            ),
            name=name,
            config=config,
        )


def _resolve_testnet_api_secret() -> str | None:
    direct_secret = os.getenv("BINANCE_TESTNET_API_SECRET")
    if direct_secret:
        return _normalize_ed25519_secret(direct_secret)

    secret_file = os.getenv(OPTIONAL_TESTNET_SECRET_FILE_ENV)
    if secret_file:
        secret_path = Path(secret_file).expanduser()
        if secret_path.exists():
            return _normalize_ed25519_secret(secret_path.read_text(encoding="utf-8"))
    return None


def _normalize_ed25519_secret(secret_text: str) -> str:
    secret = secret_text.strip()
    if "BEGIN PRIVATE KEY" not in secret:
        return secret

    lines = [
        line.strip()
        for line in secret.splitlines()
        if line.strip() and not line.startswith("-----BEGIN") and not line.startswith("-----END")
    ]
    return "".join(lines)


def validate_spot_testnet_env() -> tuple[str, str]:
    missing = [name for name in REQUIRED_TESTNET_ENV_VARS if not os.getenv(name)]
    api_secret = _resolve_testnet_api_secret()
    if api_secret is None:
        missing.append("BINANCE_TESTNET_API_SECRET or BINANCE_TESTNET_API_SECRET_FILE")
    if missing:
        missing_text = ", ".join(missing)
        raise RuntimeError(
            f"Missing Binance Spot Testnet credentials: {missing_text}. "
            "Export these variables before running the spot testnet runner.",
        )
    return os.environ["BINANCE_TESTNET_API_KEY"], api_secret


def build_spot_testnet_node(profile_name: str = "testnet") -> TradingNode:
    api_key, api_secret = validate_spot_testnet_env()
    context = build_research_context(profile_name)
    runtime = context["runtime"]
    venue = context["venue"]
    instrument_ids = [InstrumentId.from_str(asset.instrument_id) for asset in context["parameters"].assets]
    key_type = BinanceKeyType[runtime.get("key_type", "HMAC")]

    node = TradingNode(
        config=TradingNodeConfig(
            trader_id=TraderId(runtime.get("trader_id", "RBL-TESTNET-001")),
            logging=LoggingConfig(log_level=runtime.get("log_level", "INFO")),
            exec_engine=LiveExecEngineConfig(
                reconciliation=runtime.get("reconciliation", True),
                reconciliation_lookback_mins=runtime.get("reconciliation_lookback_mins", 1440),
            ),
            data_clients={
                venue: BinanceDataClientConfig(
                    api_key=api_key,
                    api_secret=api_secret,
                    key_type=key_type,
                    account_type=BinanceAccountType.SPOT,
                    us=runtime.get("us", False),
                    testnet=runtime.get("testnet", True),
                    instrument_provider=InstrumentProviderConfig(
                        load_all=runtime.get("instrument_provider_load_all", True),
                    ),
                ),
            },
            exec_clients={
                venue: BinanceExecClientConfig(
                    api_key=api_key,
                    api_secret=api_secret,
                    key_type=key_type,
                    account_type=BinanceAccountType.SPOT,
                    us=runtime.get("us", False),
                    testnet=runtime.get("testnet", True),
                    instrument_provider=InstrumentProviderConfig(
                        load_all=runtime.get("instrument_provider_load_all", True),
                    ),
                    max_retries=runtime.get("max_retries", 3),
                ),
            },
            timeout_connection=runtime.get("timeout_connection", 30.0),
            timeout_reconciliation=runtime.get("timeout_reconciliation", 10.0),
            timeout_portfolio=runtime.get("timeout_portfolio", 10.0),
            timeout_disconnection=runtime.get("timeout_disconnection", 10.0),
            timeout_post_stop=runtime.get("timeout_post_stop", 5.0),
        ),
    )

    strategy = FormalRebalancingStrategy(
        config=ResearchRebalancingConfig(
            external_order_claims=instrument_ids,
            trading=context["trading"],
            position=context["position"],
            platforms=context["platforms"],
            parameters=context["parameters"],
        )
    )
    node.trader.add_strategy(strategy)
    node.add_data_client_factory(venue, BinanceLiveDataClientFactory)
    node.add_exec_client_factory(venue, BinanceSpotTestnetCompatExecClientFactory)
    node.build()
    return node
