from __future__ import annotations

from copy import deepcopy
from pathlib import Path
from typing import Any

import yaml

from strategy_core.rebalance_strategy import ResearchAssetConfig
from strategy_core.rebalance_strategy import ResearchParametersConfig


CONFIGS_DIR = Path(__file__).resolve().parent


def profile_path(name: str) -> Path:
    candidate = Path(name)
    if candidate.is_absolute():
        return candidate
    if candidate.suffix:
        return CONFIGS_DIR / candidate.name
    return CONFIGS_DIR / f"{name}.yaml"


def _deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    merged = deepcopy(base)
    for key, value in override.items():
        if key == "extends":
            continue
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = _deep_merge(merged[key], value)
        else:
            merged[key] = deepcopy(value)
    return merged


def load_profile(name: str = "baseline") -> dict[str, Any]:
    path = profile_path(name)
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    extends = raw.get("extends")
    if extends:
        base = load_profile(str(path.parent / extends))
        return _deep_merge(base, raw)
    return raw


def build_research_context(name: str = "baseline") -> dict[str, Any]:
    values = load_profile(name)
    trading = values["trading"]
    position = values["position"]
    platforms = values["platforms"]
    research = values["research"]
    parameters = values["parameters"]
    venue = platforms["nautilus"]["venue"]
    bar_type = platforms["nautilus"]["bar_type"]
    bar_aggregation = platforms["nautilus"]["bar_aggregation"]

    assets = []
    for asset in parameters["assets"]:
        pair = asset["pair"]
        symbol = pair.replace("-", "")
        instrument_id = f"{symbol}.{venue}"
        assets.append(
            ResearchAssetConfig(
                pair=pair,
                weight=float(asset["weight"]),
                instrument_id=instrument_id,
                bar_type=f"{instrument_id}-{bar_type}-LAST-{bar_aggregation}",
            )
        )

    parameters_config = ResearchParametersConfig(
        assets=tuple(assets),
        rebalance_threshold=parameters["rebalance_threshold"],
        min_cooldown_secs=parameters["min_cooldown_secs"],
        cash_reserve_ratio=parameters["cash_reserve_ratio"],
        min_trade_value=parameters["min_trade_value"],
        max_single_trade_ratio=parameters["max_single_trade_ratio"],
        max_drawdown=parameters["max_drawdown"],
        max_daily_rebalances=parameters["max_daily_rebalances"],
        use_volatility_filter=parameters["use_volatility_filter"],
        volatility_lookback_bars=parameters["volatility_lookback_bars"],
        volatility_threshold_multiplier=parameters["volatility_threshold_multiplier"],
        use_trend_filter=parameters["use_trend_filter"],
        trend_lookback_bars=parameters["trend_lookback_bars"],
        trend_strength_buffer=parameters["trend_strength_buffer"],
        kill_switch_mode=parameters["kill_switch_mode"],
        kill_switch_resume_secs=parameters["kill_switch_resume_secs"],
        close_positions_on_stop=parameters["close_positions_on_stop"],
        post_resume_band_secs=parameters["post_resume_band_secs"],
        post_resume_throttle_secs=parameters["post_resume_throttle_secs"],
        post_resume_max_single_trade_ratio=parameters["post_resume_max_single_trade_ratio"],
    )

    return {
        "symbol": trading["pairs"][0].replace("-", ""),
        "venue": venue,
        "timeframe": research["timeframe"],
        "start_time": research["start_time"],
        "end_time": research["end_time"],
        "quote_currency": research.get("quote_currency", "USDT"),
        "trading": trading,
        "position": position,
        "platforms": platforms,
        "runtime": values.get("runtime", {}),
        "parameters": parameters_config,
    }
