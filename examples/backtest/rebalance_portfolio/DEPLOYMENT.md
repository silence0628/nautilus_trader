# Rebalance Deployment Guide

This document is the latest deployment guide for the `rebalance` strategy in
`/Users/a111/Data/wukai/nautilus_trader/examples/backtest/rebalance_portfolio`.

It covers the current working flow for:

- formal research backtests
- Binance Spot Testnet simulation
- runtime observation
- configuration and credential preparation

## 1. Deployment goals

The `rebalance` strategy must be used in this order:

1. validate strategy logic in `nautilus_trader`
2. confirm stable backtest results and reports
3. run Spot Testnet simulation
4. only after maturity, port to `philosophers-stone`
5. finally integrate into `speculum`

## 2. Current working modules

- strategy core:
  - `/Users/a111/Data/wukai/nautilus_trader/examples/backtest/rebalance_portfolio/strategy_core/rebalance_strategy.py`
- formal implementation:
  - `/Users/a111/Data/wukai/nautilus_trader/examples/backtest/rebalance_portfolio/formal_strategy.py`
- baseline config:
  - `/Users/a111/Data/wukai/nautilus_trader/examples/backtest/rebalance_portfolio/configs/baseline.yaml`
- testnet config:
  - `/Users/a111/Data/wukai/nautilus_trader/examples/backtest/rebalance_portfolio/configs/testnet.yaml`
- testnet adapter:
  - `/Users/a111/Data/wukai/nautilus_trader/examples/backtest/rebalance_portfolio/adapters/binance_testnet.py`
- testnet runner:
  - `/Users/a111/Data/wukai/nautilus_trader/examples/backtest/rebalance_portfolio/runners/run_spot_testnet.py`
- preflight check:
  - `/Users/a111/Data/wukai/nautilus_trader/examples/backtest/rebalance_portfolio/runners/check_spot_testnet_setup.py`
- reporting:
  - `/Users/a111/Data/wukai/nautilus_trader/examples/backtest/rebalance_portfolio/reporting.py`

## 3. Formal research deployment

Use this path when you want a formal research backtest with report output.

Command:

```bash
cd /Users/a111/Data/wukai/nautilus_trader
uv run --active --no-sync python examples/backtest/rebalance_portfolio/run_formal_research_baseline.py
```

Outputs:

- `/Users/a111/Data/wukai/nautilus_trader/examples/backtest/rebalance_portfolio/results/rebalance_formal_research_baseline_summary.json`
- `/Users/a111/Data/wukai/nautilus_trader/examples/backtest/rebalance_portfolio/results/backtest_report_BTCUSDT_ETHUSDT_SOLUSDT_1h_formal_research_baseline.md`

Deployment rule:

- every formal research run must output both `summary.json` and a markdown report
- markdown report structure should stay close to the team system report

## 4. Spot Testnet deployment

### 4.1 Credential standard

Required:

- `API Key`
- matching `Private Key`

Optional but recommended:

- `Public Key`

The program actually uses:

- `BINANCE_TESTNET_API_KEY`
- `BINANCE_TESTNET_API_SECRET_FILE` or `BINANCE_TESTNET_API_SECRET`

### 4.1a Register Binance Spot Testnet API

Use this full flow:

1. Generate an `Ed25519` key pair with your key generator
   - keep the `Private Key`
   - copy the `Public Key`
2. Open:
   - [https://testnet.binance.vision/](https://testnet.binance.vision/)
3. Go to `ed25519 Register Public Key`
4. Paste the generated `Public Key`
5. Complete registration
6. Binance Spot Testnet will return an `API Key`

After registration, the final usable credential set is:

- `API Key`
- matching `Private Key`
- `Public Key`

Important:

- the program signs requests with the `Private Key`
- the `Public Key` is mainly for Binance registration and later verification
- if the `API Key` and `Private Key` are not from the same key pair, auth will fail

### 4.2 Local secret files

Recommended local-only files:

- `/Users/a111/Data/wukai/nautilus_trader/examples/backtest/rebalance_portfolio/configs/testnet.env`
- `/Users/a111/Data/wukai/nautilus_trader/examples/backtest/rebalance_portfolio/configs/binance_testnet_ed25519.key`

These must remain untracked.

Recommended file content:

`/Users/a111/Data/wukai/nautilus_trader/examples/backtest/rebalance_portfolio/configs/testnet.env`

```bash
BINANCE_TESTNET_API_KEY=your_api_key
BINANCE_TESTNET_API_SECRET_FILE=/Users/a111/Data/wukai/nautilus_trader/examples/backtest/rebalance_portfolio/configs/binance_testnet_ed25519.key
```

`/Users/a111/Data/wukai/nautilus_trader/examples/backtest/rebalance_portfolio/configs/binance_testnet_ed25519.key`

```text
-----BEGIN PRIVATE KEY-----
your_private_key_body
-----END PRIVATE KEY-----
```

Not recommended:

- placing the full private key directly into the `.env` file

Recommended:

- store `API Key` in `testnet.env`
- store the `Private Key` in a separate `.key` file
- point `BINANCE_TESTNET_API_SECRET_FILE` to that file

### 4.3 Preflight

```bash
cd /Users/a111/Data/wukai/nautilus_trader
uv run --active --no-sync python examples/backtest/rebalance_portfolio/runners/check_spot_testnet_setup.py
```

Expected result:

- strategy/runtime can build
- Binance Spot Testnet data/exec config is valid
- required pairs and bar types are resolved

### 4.4 Start simulation

```bash
cd /Users/a111/Data/wukai/nautilus_trader
uv run --active --no-sync python examples/backtest/rebalance_portfolio/runners/run_spot_testnet.py
```

Expected runtime signals:

- `Binance API key authenticated`
- `has trading permissions`
- `FormalRebalancingStrategy: RUNNING`
- `TradingNode: RUNNING`

## 5. Runtime observation

The strategy subscribes to `1h` bars for:

- `BTC-USDT`
- `ETH-USDT`
- `SOL-USDT`

This means strategy decisions are not expected to change every second.

Current runtime observability includes:

- minute-level heartbeat
- live prices for `BTC/ETH/SOL`
- latest bar status
- current portfolio value summary
- rebalance count
- kill switch status
- rebalance plan summary when applicable
- order submission summary when applicable

Example heartbeat:

```text
[HEARTBEAT 2026-03-27 18:41:12] BTC-USDT=66945.44 | ETH-USDT=2006.5300 | SOL-USDT=83.4400 | latest_bar=waiting-first-1h-bar | portfolio=0.00 | max_dd=0.00% | rebalances=0 | kill_switch=False
```

Interpretation:

- prices changing means the process is alive
- `waiting-first-1h-bar` means the runner is still waiting for the first actionable `1h` bar inside the strategy flow
- no rebalance yet does not mean the program is stuck

## 6. Current deployment compatibility note

Binance Spot Testnet currently does not behave well with the legacy `listenKey`
bootstrap flow used by older paths.

Current local solution:

- the adapter skips old Spot Testnet `listenKey` bootstrap
- execution continues in compatibility mode via REST + reconciliation

This is implemented in:

- `/Users/a111/Data/wukai/nautilus_trader/examples/backtest/rebalance_portfolio/adapters/binance_testnet.py`

## 7. What to change when strategy evolves

If strategy logic changes:

- update `/Users/a111/Data/wukai/nautilus_trader/examples/backtest/rebalance_portfolio/formal_strategy.py`

If only parameters change:

- update `/Users/a111/Data/wukai/nautilus_trader/examples/backtest/rebalance_portfolio/configs/baseline.yaml`
- update `/Users/a111/Data/wukai/nautilus_trader/examples/backtest/rebalance_portfolio/configs/testnet.yaml`

If exchange/runtime behavior changes:

- update `/Users/a111/Data/wukai/nautilus_trader/examples/backtest/rebalance_portfolio/adapters/binance_testnet.py`
- update `/Users/a111/Data/wukai/nautilus_trader/examples/backtest/rebalance_portfolio/runners/run_spot_testnet.py`

## 8. Recommended deployment sequence

1. run formal research baseline
2. review `summary.json`
3. review markdown report
4. run Spot Testnet preflight
5. start Spot Testnet simulation
6. observe heartbeat and price board
7. confirm rebalance plan / order logs
8. only then move toward longer simulation windows

## 9. Common troubleshooting

### 9.1 No strategy movement for several minutes

This is normal when:

- the strategy is waiting for its first `1h` bar
- no rebalance condition is met yet

Use heartbeat, not silence, as the running indicator.

### 9.2 Prices are moving but `portfolio=0.00`

This usually means:

- the strategy has not processed its first synchronized bar snapshot yet

### 9.3 Exec auth failure

Check:

- correct `API Key`
- matching `Private Key`
- trading permission enabled
- correct Testnet environment

### 9.4 Signature failure

Check:

- the private key matches the API key
- PEM formatting is correct
- local secret file path is correct

## 10. Current standard

For `rebalance` in `nautilus_trader`, the standard is now:

- modular strategy layout
- config-first parameter changes
- report-first formal backtests
- observable Spot Testnet runtime
- no direct dependence on team system for strategy truth
