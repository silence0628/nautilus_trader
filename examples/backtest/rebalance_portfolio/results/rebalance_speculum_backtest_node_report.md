# Rebalance Official BacktestNode 验证报告

## 1. 目的

验证是否可以使用 Nautilus 官方推荐的高层 API：

- `ParquetDataCatalog`
- `BacktestDataConfig`
- `ImportableStrategyConfig`
- `BacktestNode`

来替代当前的 `speculum` adapter 对齐脚本，作为 `rebalance` 的正式研究入口。

## 2. 执行入口

- `/Users/a111/Data/wukai/nautilus_trader/examples/backtest/rebalance_portfolio/run_speculum_backtest_node.py`

## 3. 当前结果

当前高层 API 入口已经可以成功执行，并生成结果文件：

- `/Users/a111/Data/wukai/nautilus_trader/examples/backtest/rebalance_portfolio/results/rebalance_speculum_backtest_node_summary.json`

但结果与团队正式基线**尚未对齐**。

## 4. 当前输出摘要

高层 API 当前输出大致为：

- `total_orders = 74`
- `total_positions = 30`
- `stats_returns.sharpe_ratio ≈ 9.0172`

而团队正式基线是：

- `final_balance = 25165.16741205`
- `total_return_pct = 151.6516741205`
- `total_trades = 27`
- `sharpe_ratio = 5.0949200464016196`

## 5. 当前判断

这说明：

1. 官方高层 API 路径已经“能跑”。
2. 但它现在还不能直接替代当前 adapter 对齐路径。
3. 对我们当前阶段来说，“结果一致性”优先级高于“组织形式官方化”。

## 6. 当前建议

当前建议分成两条线：

### 正式基线

继续使用：

- `/Users/a111/Data/wukai/nautilus_trader/examples/backtest/rebalance_portfolio/run_speculum_adapter_backtest.py`

原因：

- 它已经与 `speculum` 团队正式回测结果对齐

### 后续研究方向

继续保留并研究：

- `/Users/a111/Data/wukai/nautilus_trader/examples/backtest/rebalance_portfolio/run_speculum_backtest_node.py`

原因：

- 它更符合 Nautilus 官方原生高层 API 风格
- 但还需要继续找出和团队基线不一致的根因

## 7. 阶段结论

目前可以说：

- 官方原生风格入口已经建立
- 但尚未达到“可以替代正式对齐入口”的程度

因此现阶段最稳妥的结论是：

- **官方高层 API 路径保留继续研究**
- **正式结果仍以 adapter 对齐路径为准**
