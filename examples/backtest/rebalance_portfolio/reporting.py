from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any
import json

import numpy as np
import pandas as pd


def dataframe_to_markdown(df: pd.DataFrame) -> str:
    if df.empty:
        return "_No data_"
    try:
        return df.to_markdown(index=False)
    except ImportError:
        headers = [str(column) for column in df.columns]
        separator = ["---"] * len(headers)
        rows = [
            "| " + " | ".join(headers) + " |",
            "| " + " | ".join(separator) + " |",
        ]
        for record in df.astype(object).itertuples(index=False, name=None):
            values = ["-" if value is None else str(value) for value in record]
            rows.append("| " + " | ".join(values) + " |")
        return "\n".join(rows)


def _to_decimal(value: Any) -> Decimal:
    if value is None:
        return Decimal("0")
    if isinstance(value, Decimal):
        return value
    text = str(value).strip()
    if " " in text:
        text = text.split(" ", 1)[0]
    if text in {"", "None", "nan", "NaN"}:
        return Decimal("0")
    return Decimal(text)


def _to_float(value: Any) -> float:
    return float(_to_decimal(value))


def _format_pct(value: float | Decimal | None) -> str:
    if value is None:
        return "-"
    return f"{float(value):+.2f}%"


def _format_num(value: float | Decimal | None, digits: int = 2) -> str:
    if value is None:
        return "-"
    return f"{float(value):.{digits}f}"


def _format_money(value: float | Decimal | None, currency: str = "USDT") -> str:
    if value is None:
        return "-"
    return f"{float(value):.2f} {currency}"


def build_equity_curve_dataframe(portfolio_points: list[dict[str, Any]]) -> pd.DataFrame:
    return build_equity_curve_dataframe_with_initial(portfolio_points)


def build_equity_curve_dataframe_with_initial(
    portfolio_points: list[dict[str, Any]],
    initial_timestamp: pd.Timestamp | None = None,
    initial_value: float | None = None,
) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    if initial_timestamp is not None and initial_value is not None:
        rows.append(
            {
                "timestamp": pd.to_datetime(initial_timestamp, utc=True),
                "portfolio_value": float(initial_value),
            }
        )

    for point in portfolio_points:
        ts = point.get("ts") or point.get("timestamp") or point.get("time")
        if ts is None:
            continue
        if isinstance(ts, (int, float)):
            timestamp = pd.to_datetime(ts, unit="s", utc=True)
        else:
            timestamp = pd.to_datetime(ts, utc=True)
        rows.append(
            {
                "timestamp": timestamp,
                "portfolio_value": float(point["value"]),
            }
        )

    df = pd.DataFrame(rows)
    if df.empty:
        return df
    return df.sort_values("timestamp").drop_duplicates(subset=["timestamp"], keep="first").reset_index(drop=True)


def calculate_performance_metrics(
    equity_df: pd.DataFrame,
    initial_capital: float,
    max_drawdown_pct: float,
) -> dict[str, float | None]:
    if equity_df.empty:
        return {
            "net_profit": 0.0,
            "total_return_pct": 0.0,
            "annualized_return_pct": 0.0,
            "volatility_pct": 0.0,
            "sharpe_ratio": 0.0,
            "sortino_ratio": 0.0,
            "calmar_ratio": 0.0,
        }

    final_balance = float(equity_df["portfolio_value"].iloc[-1])
    net_profit = final_balance - initial_capital
    total_return_pct = (net_profit / initial_capital) * 100

    start_ts = equity_df["timestamp"].iloc[0]
    end_ts = equity_df["timestamp"].iloc[-1]
    total_days = max((end_ts - start_ts).total_seconds() / 86400, 1.0)
    annualized_return_pct = (((final_balance / initial_capital) ** (365.0 / total_days)) - 1.0) * 100

    daily_equity = (
        equity_df.set_index("timestamp")["portfolio_value"].resample("1D").last().dropna()
    )
    daily_returns = daily_equity.pct_change().dropna()

    if daily_returns.empty or float(daily_returns.std()) == 0:
        volatility_pct = 0.0
        sharpe_ratio = 0.0
        sortino_ratio = 0.0
    else:
        volatility_pct = float(daily_returns.std() * np.sqrt(365) * 100)
        sharpe_ratio = float((daily_returns.mean() / daily_returns.std()) * np.sqrt(365))
        downside = daily_returns[daily_returns < 0]
        if downside.empty or float(downside.std()) == 0:
            sortino_ratio = 0.0
        else:
            sortino_ratio = float((daily_returns.mean() / downside.std()) * np.sqrt(365))

    calmar_ratio = 0.0 if max_drawdown_pct == 0 else annualized_return_pct / abs(max_drawdown_pct)

    return {
        "net_profit": net_profit,
        "total_return_pct": total_return_pct,
        "annualized_return_pct": annualized_return_pct,
        "volatility_pct": volatility_pct,
        "sharpe_ratio": sharpe_ratio,
        "sortino_ratio": sortino_ratio,
        "calmar_ratio": calmar_ratio,
    }


def calculate_trade_statistics(positions_report: pd.DataFrame) -> dict[str, Any]:
    if positions_report.empty:
        return {
            "total_trades": 0,
            "winning_trades": 0,
            "losing_trades": 0,
            "win_rate": 0.0,
            "profit_factor": 0.0,
            "average_duration_hours": None,
            "long_trades": 0,
            "long_winning_trades": 0,
            "long_win_rate": 0.0,
            "long_total_pnl": 0.0,
            "short_trades": 0,
            "short_winning_trades": 0,
            "short_win_rate": 0.0,
            "short_total_pnl": 0.0,
        }

    df = positions_report.copy()
    if "is_snapshot" in df.columns:
        df = df[df["is_snapshot"] == True].copy()
    df = df[df["ts_closed"].notna()].copy()
    if df.empty:
        return {
            "total_trades": 0,
            "winning_trades": 0,
            "losing_trades": 0,
            "win_rate": 0.0,
            "profit_factor": 0.0,
            "average_duration_hours": None,
            "long_trades": 0,
            "long_winning_trades": 0,
            "long_win_rate": 0.0,
            "long_total_pnl": 0.0,
            "short_trades": 0,
            "short_winning_trades": 0,
            "short_win_rate": 0.0,
            "short_total_pnl": 0.0,
        }
    df["realized_pnl_value"] = df["realized_pnl"].apply(_to_float)
    df["realized_return_pct"] = df["realized_return"].apply(_to_float) * 100
    df["duration_hours"] = pd.to_numeric(df["duration_ns"], errors="coerce") / 3_600_000_000_000
    df["entry_side"] = df["entry"].astype(str)

    total_trades = int(len(df))
    winning = df[df["realized_pnl_value"] > 0]
    losing = df[df["realized_pnl_value"] < 0]
    gross_profit = float(winning["realized_pnl_value"].sum())
    gross_loss = abs(float(losing["realized_pnl_value"].sum()))

    long_df = df[df["entry_side"] == "BUY"]
    short_df = df[df["entry_side"] == "SELL"]

    return {
        "total_trades": total_trades,
        "winning_trades": int(len(winning)),
        "losing_trades": int(len(losing)),
        "win_rate": (len(winning) / total_trades * 100) if total_trades else 0.0,
        "profit_factor": (gross_profit / gross_loss) if gross_loss > 0 else 0.0,
        "average_duration_hours": float(df["duration_hours"].mean()) if total_trades else None,
        "long_trades": int(len(long_df)),
        "long_winning_trades": int(len(long_df[long_df["realized_pnl_value"] > 0])),
        "long_win_rate": (
            len(long_df[long_df["realized_pnl_value"] > 0]) / len(long_df) * 100
            if len(long_df)
            else 0.0
        ),
        "long_total_pnl": float(long_df["realized_pnl_value"].sum()),
        "short_trades": int(len(short_df)),
        "short_winning_trades": int(len(short_df[short_df["realized_pnl_value"] > 0])),
        "short_win_rate": (
            len(short_df[short_df["realized_pnl_value"] > 0]) / len(short_df) * 100
            if len(short_df)
            else 0.0
        ),
        "short_total_pnl": float(short_df["realized_pnl_value"].sum()),
    }


def build_monthly_returns(
    equity_df: pd.DataFrame,
    initial_capital: float,
    timezone_name: str = "Asia/Shanghai",
) -> pd.DataFrame:
    if equity_df.empty:
        return pd.DataFrame(columns=["month", "return_pct"])

    localized = equity_df.copy()
    localized["local_timestamp"] = localized["timestamp"].dt.tz_convert(timezone_name)
    localized["month"] = localized["local_timestamp"].dt.strftime("%Y-%m")

    rows: list[dict[str, Any]] = []
    for month, group in localized.groupby("month", sort=True):
        start_value = float(group["portfolio_value"].iloc[0])
        end_value = float(group["portfolio_value"].iloc[-1])
        return_pct = ((end_value - start_value) / start_value) * 100 if start_value > 0 else 0.0
        rows.append({"month": month, "return_pct": return_pct})

    return pd.DataFrame(rows)


def build_monthly_returns_from_equity_points(
    equity_points: list[dict[str, Any]],
    timezone_name: str = "Asia/Shanghai",
) -> pd.DataFrame:
    if not equity_points:
        return pd.DataFrame(columns=["month", "return_pct"])

    df = pd.DataFrame(equity_points)
    if df.empty or "timestamp" not in df.columns:
        return pd.DataFrame(columns=["month", "return_pct"])

    df["timestamp"] = pd.to_datetime(df["timestamp"], utc=True, errors="coerce")
    df = df.dropna(subset=["timestamp"]).copy()
    if df.empty:
        return pd.DataFrame(columns=["month", "return_pct"])

    value_column = "equity" if "equity" in df.columns else "balance"
    df[value_column] = pd.to_numeric(df[value_column], errors="coerce")
    df = df.dropna(subset=[value_column]).sort_values("timestamp").copy()
    if df.empty:
        return pd.DataFrame(columns=["month", "return_pct"])

    df["local_timestamp"] = df["timestamp"].dt.tz_convert(timezone_name)
    df["month"] = df["local_timestamp"].dt.strftime("%Y-%m")

    rows: list[dict[str, Any]] = []
    for month, group in df.groupby("month", sort=True):
        start_value = float(group[value_column].iloc[0])
        end_value = float(group[value_column].iloc[-1])
        return_pct = ((end_value - start_value) / start_value) * 100 if start_value > 0 else 0.0
        rows.append({"month": month, "return_pct": return_pct})

    return pd.DataFrame(rows)


def build_monthly_returns_from_indicator_points(
    indicator_points: list[dict[str, Any]],
    timezone_name: str = "Asia/Shanghai",
    initial_timestamp: Any | None = None,
    initial_value: float | None = None,
) -> pd.DataFrame:
    if not indicator_points and (initial_timestamp is None or initial_value is None):
        return pd.DataFrame(columns=["month", "return_pct"])

    rows: list[dict[str, Any]] = []
    if initial_timestamp is not None and initial_value is not None:
        timestamp = pd.to_datetime(initial_timestamp, utc=True, errors="coerce")
        if not pd.isna(timestamp):
            rows.append({"timestamp": timestamp, "value": float(initial_value)})

    for point in indicator_points:
        ts = point.get("time") or point.get("timestamp") or point.get("ts")
        value = point.get("value")
        if ts is None or value is None:
            continue
        if isinstance(ts, (int, float)):
            timestamp = pd.to_datetime(int(ts), unit="s", utc=True)
        else:
            timestamp = pd.to_datetime(ts, utc=True, errors="coerce")
        if pd.isna(timestamp):
            continue
        rows.append({"timestamp": timestamp, "value": float(value)})

    if not rows:
        return pd.DataFrame(columns=["month", "return_pct"])

    df = pd.DataFrame(rows).sort_values("timestamp", kind="mergesort").reset_index(drop=True)
    df["local_timestamp"] = df["timestamp"].dt.tz_convert(timezone_name)
    df["month"] = df["local_timestamp"].dt.strftime("%Y-%m")

    monthly_map: dict[str, dict[str, float]] = {}
    for row in df.itertuples(index=False):
        month_key = row.month
        value = float(row.value)
        if month_key not in monthly_map:
            monthly_map[month_key] = {"start": value, "end": value, "count": 1}
        else:
            monthly_map[month_key]["end"] = value
            monthly_map[month_key]["count"] += 1

    entries = list(monthly_map.items())
    if entries and entries[-1][1]["count"] == 1:
        entries = entries[:-1]
    elif entries:
        last_month, _ = entries[-1]
        last_timestamp = df["local_timestamp"].iloc[-1]
        if last_timestamp.strftime("%Y-%m") == last_month and last_timestamp.day == 1:
            entries = entries[:-1]

    return pd.DataFrame(
        [
            {
                "month": month,
                "return_pct": (
                    ((data["end"] - data["start"]) / data["start"]) * 100 if data["start"] > 0 else 0.0
                ),
            }
            for month, data in entries
        ]
    )


def build_recent_trades_table(positions_report: pd.DataFrame, limit: int = 20) -> pd.DataFrame:
    if positions_report.empty:
        return pd.DataFrame(
            columns=["#", "方向", "入场时间", "入场价", "出场时间", "出场价", "盈亏", "盈亏%"]
        )

    df = positions_report.copy()
    if "is_snapshot" in df.columns:
        df = df[df["is_snapshot"] == True].copy()
    df = df[df["ts_closed"].notna()].copy()
    df["opened_at"] = pd.to_datetime(df["ts_opened"], utc=True)
    df["closed_at"] = pd.to_datetime(df["ts_closed"], utc=True)
    df["realized_pnl_value"] = df["realized_pnl"].apply(_to_float)
    df["realized_return_pct"] = df["realized_return"].apply(_to_float) * 100
    df = df.sort_values("closed_at").tail(limit).reset_index(drop=True)

    total_count = len(df)
    recent_df = df.tail(limit).reset_index(drop=True)
    start_index = max(total_count - len(recent_df) + 1, 1)
    rows = []
    for idx, row in enumerate(recent_df.itertuples(index=False), start=start_index):
        rows.append(
            {
                "#": idx,
                "方向": "LONG" if str(row.entry) == "BUY" else "SHORT",
                "入场时间": row.opened_at.isoformat().replace("+00:00", "")[:19],
                "入场价": _format_num(row.avg_px_open, 2),
                "出场时间": row.closed_at.isoformat().replace("+00:00", "")[:19],
                "出场价": _format_num(row.avg_px_close, 2),
                "盈亏": _format_num(row.realized_pnl_value, 2),
                "盈亏%": _format_pct(row.realized_return_pct),
            }
        )
    return pd.DataFrame(rows)


def render_formal_markdown_report(
    *,
    title: str,
    generated_at: datetime,
    context: dict[str, Any],
    strategy_config: dict[str, Any],
    metrics: dict[str, Any],
    trade_stats: dict[str, Any],
    monthly_returns: pd.DataFrame,
    recent_trades: pd.DataFrame,
) -> str:
    lines = [
        f"# 回测报告: {title}",
        "",
        f"> 生成时间: {generated_at.astimezone(UTC).strftime('%Y-%m-%d %H:%M:%S')}",
        "",
        "---",
        "",
        "## 1. 基本信息",
        "",
        "| 项目 | 值 |",
        "|------|-----|",
        f"| 策略名称 | Rebalancing (Nautilus) |",
        f"| 交易品种 | {', '.join(context['pairs'])} |",
        f"| 交易所 | {context['venue']} |",
        f"| K线周期 | {context['timeframe']} |",
        f"| 测试周期 | {context['start_time']} ~ {context['end_time']} |",
        f"| 初始资金 | {float(context['initial_capital']):.2f} USDT |",
        f"| 回测状态 | COMPLETED |",
        "",
        "---",
        "",
        "## 2. 策略配置",
        "",
        "```json",
        json.dumps(strategy_config, ensure_ascii=False, indent=2),
        "```",
        "",
        "---",
        "",
        "## 3. 核心绩效指标",
        "",
        "### 3.1 收益指标",
        "",
        "| 指标 | 值 | 说明 |",
        "|------|-----|------|",
        f"| 净利润 | {_format_money(metrics['net_profit'])} | 总盈亏金额 |",
        f"| 总收益率 | {_format_pct(metrics['total_return_pct'])} | 相对初始资金 |",
        f"| 年化收益率 | {_format_pct(metrics['annualized_return_pct'])} | 年化后收益 |",
        f"| 最大回撤 | {_format_pct(metrics['max_drawdown_pct'])} | 最大亏损幅度 |",
        f"| 最终余额 | {_format_money(metrics['final_balance'])} | 回测结束时余额 |",
        "",
        "### 3.2 风险指标",
        "",
        "| 指标 | 值 | 说明 |",
        "|------|-----|------|",
        f"| 夏普比率 | {_format_num(metrics['sharpe_ratio'])} | 风险调整收益 |",
        f"| 索提诺比率 | {_format_num(metrics['sortino_ratio'])} | 下行风险调整收益 |",
        f"| 卡玛比率 | {_format_num(metrics['calmar_ratio'])} | 收益/最大回撤 |",
        f"| 波动率 | {_format_pct(metrics['volatility_pct'])} | 收益波动程度 |",
        f"| 最大回撤时长 | - | 最长回撤持续时间 |",
        "",
        "### 3.3 交易统计",
        "",
        "| 指标 | 值 | 说明 |",
        "|------|-----|------|",
        f"| 总交易次数 | {trade_stats['total_trades']} | 已平仓交易 |",
        f"| 盈利次数 | {trade_stats['winning_trades']} | 盈利交易数 |",
        f"| 亏损次数 | {trade_stats['losing_trades']} | 亏损交易数 |",
        f"| 胜率 | {_format_pct(trade_stats['win_rate'])} | 盈利交易占比 |",
        f"| 利润因子 | {_format_num(trade_stats['profit_factor'])} | 总盈利/总亏损 |",
        f"| 平均持仓时长 | {_format_num(trade_stats['average_duration_hours'])} | 每笔交易平均持仓（小时） |",
        "",
        "---",
        "",
        "## 4. 多空分析",
        "",
        "### 4.1 做多交易",
        "",
        "| 指标 | 值 |",
        "|------|-----|",
        f"| 总次数 | {trade_stats['long_trades']} |",
        f"| 盈利次数 | {trade_stats['long_winning_trades']} |",
        f"| 胜率 | {_format_pct(trade_stats['long_win_rate'])} |",
        f"| 总盈亏 | {_format_money(trade_stats['long_total_pnl'])} |",
        "",
        "### 4.2 做空交易",
        "",
        "| 指标 | 值 |",
        "|------|-----|",
        f"| 总次数 | {trade_stats['short_trades']} |",
        f"| 盈利次数 | {trade_stats['short_winning_trades']} |",
        f"| 胜率 | {_format_pct(trade_stats['short_win_rate'])} |",
        f"| 总盈亏 | {_format_money(trade_stats['short_total_pnl'])} |",
        "",
        "---",
        "",
        "## 5. 月度收益分析",
        "",
        "",
        "| 月份 | 收益率 |",
        "|------|--------|",
    ]

    if monthly_returns.empty:
        lines.append("| - | - |")
    else:
        for row in monthly_returns.itertuples(index=False):
            lines.append(f"| {row.month} | {_format_pct(row.return_pct)} |")

    lines.extend(
        [
            "",
            "",
            "---",
            "",
            "## 6. 交易明细 (最近20笔)",
            "",
        ]
    )

    if recent_trades.empty:
        lines.append("_无交易明细_")
    else:
        lines.append(dataframe_to_markdown(recent_trades))

    lines.extend(
        [
            "",
            "",
            "---",
            "",
            "## 7. 结论与建议",
            "",
            "### 7.1 策略表现总结",
            "",
            f"- **收益能力**: 总收益率 {_format_pct(metrics['total_return_pct'])}, 年化收益 {_format_pct(metrics['annualized_return_pct'])}",
            f"- **风险控制**: 最大回撤 {_format_pct(metrics['max_drawdown_pct'])}, 夏普比率 {_format_num(metrics['sharpe_ratio'])}",
            f"- **交易效率**: 胜率 {_format_pct(trade_stats['win_rate'])}, 盈利因子 {_format_num(trade_stats['profit_factor'])}",
            "",
            "### 7.2 注意事项",
            "",
            "1. 本报告由 nautilus_trader 研究层生成，用于策略研发与团队系统结果对照。",
            "2. 历史表现不代表未来收益，实盘仍需进一步验证滑点、延迟和真实成交环境。",
            "3. 后续参数寻优、IOS、Walk-Forward、Monte Carlo 应继续基于这条正式研究基线推进。",
            "",
            "---",
            "",
            "*本报告由 Nautilus 研究回测链路自动生成 | 对齐目标: Speculum / Philosophers-Stone*",
        ]
    )

    return "\n".join(lines)


def standard_report_paths(results_dir: Path, title_slug: str) -> tuple[Path, Path]:
    summary_path = results_dir / f"{title_slug}_summary.json"
    report_path = results_dir / f"backtest_report_{title_slug}.md"
    return summary_path, report_path
