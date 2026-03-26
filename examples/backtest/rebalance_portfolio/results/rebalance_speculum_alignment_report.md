# Rebalance Speculum 对齐结果报告

## 1. 目标

确认 `/Users/a111/Data/wukai/nautilus_trader` 中的 `rebalance` 回测，
能够与 `/Users/a111/Data/wukai/speculum` 的真实团队回测结果对齐，
并明确后续参数寻优应以哪条路径为正式基线。

## 2. 对齐口径

- 数据来源：`speculum` PostgreSQL 中的 `market_datasets + ohlcv_data`
- 交易所：`BINANCE`
- 市场类型：Spot
- 周期：`1h`
- 资产：`BTCUSDT / ETHUSDT / SOLUSDT`
- 时间范围：`2022-12-31 00:00:00+00:00 ~ 2026-01-01 00:00:00+00:00`
- 参数来源：`speculum` 回测记录 `069a7ea6-5737-47bd-b7f2-4dce09d4a91e`
- 配置构建：复用 `speculum` 的 `NautilusAdapter + DataLoader + build_strategy_config`

## 3. 结果

使用入口：

```bash
python /Users/a111/Data/wukai/nautilus_trader/examples/backtest/rebalance_portfolio/run_speculum_adapter_backtest.py
```

输出文件：

- `/Users/a111/Data/wukai/nautilus_trader/examples/backtest/rebalance_portfolio/results/rebalance_speculum_adapter_backtest_summary.json`

关键结果：

| 指标 | 对齐结果 |
| --- | --- |
| final_balance | `25165.16741205` |
| total_return_pct | `151.6516741205` |
| peak_balance | `29579.45469886` |
| max_drawdown_pct | `13.16585146307821` |
| total_trades | `27` |
| sharpe_ratio | `5.0949200464016196` |
| profit_factor | `3.4182121070616414` |

## 4. 关键发现

### 4.1 之前的“对齐脚本”并不是真正对齐

之前使用 `/Users/a111/Data/wukai/philosophers-stone/portfolio/rebalancing/refinement/nautilus/run_smoke_backtest.py`
中的 CSV/smoke 转换辅助函数时，得到的结果约为：

- `final_balance ≈ 29047.44`
- `total_return_pct ≈ 190.47%`

这条结果明显高于团队系统真实结果。

根因不是参数不同，而是数据转换链路不同：

- `stone` smoke helper
- `speculum` adapter 的 `dataframe_to_nautilus_bars / quotes`

两者对 bar open/close 时间的处理方式不同，不能直接混用来做正式对照。

### 4.2 团队系统里还存在一个结果存储缩放 bug

`speculum` 数据库里 `backtest_results.total_return_pct` 被错误除以了 `100`，
但 `full_metrics.total_return` 是正确的。

已经定位到：

- `/Users/a111/Data/wukai/speculum/backend/app/engine/backtest_runner.py`

当前正式判断时，应以：

- `full_metrics.total_return`
- 或本次 `speculum adapter` 直接重跑结果

作为真实百分比口径。

## 5. 当前结论

当前可以正式确认：

1. `rebalance` 在 `nautilus_trader` 中已经可以走真实数据对齐路径。
2. 与 `speculum` 团队系统的正式回测结果可以对齐。
3. 后续参数寻优、IOS、Walk-Forward、Monte Carlo 必须基于这条对齐路径继续。
4. 合成数据和 smoke helper 结果只能用于技术验证，不能再用于正式策略结论。
