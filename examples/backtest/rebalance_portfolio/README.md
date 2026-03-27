# Rebalance Portfolio Backtest

This directory contains the `rebalance` research workspace for
`/Users/a111/Data/wukai/nautilus_trader`.

## Scope

- keep the strategy isolated from shared platform internals
- reproduce the team `speculum` backtest baseline with real Binance Spot 1h data
- provide a repeatable foundation for later parameter sweeps and robustness tests

## Current entrypoints

### 0. Formal research baseline

This is now the primary research-side entrypoint.
It uses the formal `stone` strategy logic ported into `nautilus_trader`, reads the
formal default parameters from `philosophers-stone/portfolio/rebalancing/config.yaml`,
and runs on real Binance Spot `1h` data.

Output file:

- `/Users/a111/Data/wukai/nautilus_trader/examples/backtest/rebalance_portfolio/results/rebalance_formal_research_baseline_summary.json`

```bash
uv run --active --no-sync python examples/backtest/rebalance_portfolio/run_formal_research_baseline.py
```

Important:

- this is the strategy-first research entrypoint
- it should be treated as the future mainline for `nautilus_trader` rebalance research
- `stone` / `speculum` should later consume mature conclusions from here, not define them

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
