# Rebalance Portfolio Backtest

This directory contains the `rebalance` research workspace for
`/Users/a111/Data/wukai/nautilus_trader`.

## Scope

- keep the strategy isolated from shared platform internals
- reproduce the team `speculum` backtest baseline with real Binance Spot 1h data
- provide a repeatable foundation for later parameter sweeps and robustness tests
- prepare a modular path for later Binance Spot Testnet execution

## Directory structure

- `strategy_core/`
  - research/live shared rebalance core entrypoint
- `configs/`
  - `baseline.yaml`: research baseline profile
  - `testnet.yaml`: Binance Spot Testnet runtime profile
- `adapters/`
  - exchange/runtime builders, currently Binance Spot Testnet
- `runners/`
  - mode-specific launchers
- `reports/`
  - normalized report documents
- `logs/`
  - live/testnet diagnostic outputs
- `results/`
  - legacy generated backtest artifacts kept for compatibility

## Current entrypoints

### 0. Formal research baseline

This is now the primary research-side entrypoint.
It uses the formal `stone` strategy logic ported into `nautilus_trader`, reads the
formal default parameters from `philosophers-stone/portfolio/rebalancing/config.yaml`,
and runs on real Binance Spot `1h` data.

Output file:

- `/Users/a111/Data/wukai/nautilus_trader/examples/backtest/rebalance_portfolio/results/rebalance_formal_research_baseline_summary.json`
- `/Users/a111/Data/wukai/nautilus_trader/examples/backtest/rebalance_portfolio/results/backtest_report_BTCUSDT_ETHUSDT_SOLUSDT_1h_formal_research_baseline.md`

```bash
uv run --active --no-sync python examples/backtest/rebalance_portfolio/run_formal_research_baseline.py
```

Important:

- this is the strategy-first research entrypoint
- it should be treated as the future mainline for `nautilus_trader` rebalance research
- `stone` / `speculum` should later consume mature conclusions from here, not define them
- every formal research backtest should output both `summary.json` and a full markdown report
- the markdown report should follow the team report structure as closely as possible:
  basic info, strategy config, core metrics, trade stats, monthly returns, recent trades, and conclusion
- core portfolio metrics must stay aligned with the team backtest baseline when the same config is used

### 0b. Binance Spot Testnet runner

This is the first live-oriented modular runner.
It uses the same `strategy_core` logic, but swaps the runner/config layer to a
`TradingNode` with Binance Spot Testnet clients.

```bash
uv run --active --no-sync python examples/backtest/rebalance_portfolio/runners/run_spot_testnet.py
```

Notes:

- credentials are sourced from the standard Binance Testnet environment variables
- strategy logic should not change between backtest and testnet, only the profile/runner
- future parameter updates should happen in `configs/*.yaml`, not by patching the strategy core
- required variables:
  - `BINANCE_TESTNET_API_KEY`
  - `BINANCE_TESTNET_API_SECRET` or `BINANCE_TESTNET_API_SECRET_FILE`
- example env template:
  - `configs/testnet.env.example`
- optional local file:
  - `configs/testnet.env`
- recommended for `ED25519`:
  - store the private key in a local `*.key` file and reference it with `BINANCE_TESTNET_API_SECRET_FILE`

Preflight check:

```bash
cp examples/backtest/rebalance_portfolio/configs/testnet.env.example \
   examples/backtest/rebalance_portfolio/configs/testnet.env

uv run --active --no-sync python examples/backtest/rebalance_portfolio/runners/check_spot_testnet_setup.py
```

### 1. Local deterministic baseline

Used only to verify the standalone research skeleton is runnable.

```bash
uv run --active --no-sync python examples/backtest/rebalance_portfolio/run_baseline_backtest.py
```

### 2. First parameter sweep

This synthetic sweep is kept only as an early technical example.

```bash
uv run --active --no-sync python examples/backtest/rebalance_portfolio/run_parameter_sweep.py
```

### 3. Real-data parameter sweep on the aligned baseline

This is the preferred stage-2 polishing entrypoint after the aligned baseline is stable.
It reuses the team `speculum` backend adapter and the same real Binance Spot 1h data/config baseline.

Output files:

- `/Users/a111/Data/wukai/nautilus_trader/examples/backtest/rebalance_portfolio/results/rebalance_speculum_adapter_parameter_sweep.json`
- `/Users/a111/Data/wukai/nautilus_trader/examples/backtest/rebalance_portfolio/results/rebalance_speculum_adapter_parameter_sweep.csv`
- `/Users/a111/Data/wukai/nautilus_trader/examples/backtest/rebalance_portfolio/results/rebalance_speculum_adapter_parameter_sweep.md`

```bash
python examples/backtest/rebalance_portfolio/run_speculum_adapter_parameter_sweep.py
```

### 4. Exact `speculum` adapter alignment

This is the formal comparison entrypoint.
It reuses the team `speculum` backend adapter, database data, and strategy config
through `docker exec speculum-backend`, then writes the aligned result to:

- `/Users/a111/Data/wukai/nautilus_trader/examples/backtest/rebalance_portfolio/results/rebalance_speculum_adapter_backtest_summary.json`

```bash
python examples/backtest/rebalance_portfolio/run_speculum_adapter_backtest.py
```

### 5. Official Nautilus high-level API (`BacktestNode`)

This entrypoint follows the official high-level backtesting style:

- export real data into a `ParquetDataCatalog`
- configure `BacktestDataConfig`
- configure `ImportableStrategyConfig`
- run via `BacktestNode`

Output file:

- `/Users/a111/Data/wukai/nautilus_trader/examples/backtest/rebalance_portfolio/results/rebalance_speculum_backtest_node_summary.json`

```bash
uv run --active --no-sync python examples/backtest/rebalance_portfolio/run_speculum_backtest_node.py
```

Important:

- this path is a comparison tool, not the research truth source
- use it to compare `nautilus_trader` research output against team-system execution output
- do not let this path reverse-define the strategy core

## Important rule

Synthetic/local baseline results can be used for code smoke checks only.
Formal research should start from the local `nautilus_trader` formal strategy path.
Team alignment remains necessary, but must happen after the research logic is first established here.
