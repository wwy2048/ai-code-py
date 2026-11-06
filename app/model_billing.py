"""模型调用计费标准与成本计算。

维护说明：
1. 新增模型时，在 MODEL_PRICING 中添加一条记录。
2. 价格单位统一为“人民币元 / 100 万 tokens”。
3. peak 为高峰时段价格，off_peak 为空闲时段价格；没有时段差异时可设为相同值。
4. 北京时间周一至周五 09:00-12:00、14:00-18:00 为高峰时段。
5. 法定节假日请在 CN_HOLIDAYS 中人工补充，避免按工作日价格误算。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from decimal import ROUND_HALF_UP, Decimal

BILLING_CURRENCY = "CNY"
BEIJING_TIMEZONE = timezone(timedelta(hours=8), name="Asia/Shanghai")
TOKENS_PER_PRICE_UNIT = Decimal("1000000")
_COST_QUANTUM = Decimal("0.00000001")


@dataclass(frozen=True, slots=True)
class TokenRates:
    """某一价格时段的三类 token 单价。"""

    input_cache_miss_per_million: Decimal
    input_cache_hit_per_million: Decimal
    output_per_million: Decimal


@dataclass(frozen=True, slots=True)
class ModelPricing:
    peak: TokenRates
    off_peak: TokenRates


# 官方价格来源：https://api-docs.deepseek.com/zh-cn/quick_start/pricing
# 核对日期：2026-11-07。价格发生变化时直接在这里维护。
MODEL_PRICING: dict[str, ModelPricing] = {
    "deepseek-flash": ModelPricing(
        peak=TokenRates(
            input_cache_miss_per_million=Decimal("2.00"),
            input_cache_hit_per_million=Decimal("0.04"),
            output_per_million=Decimal("8.00"),
        ),
        off_peak=TokenRates(
            input_cache_miss_per_million=Decimal("1.00"),
            input_cache_hit_per_million=Decimal("0.02"),
            output_per_million=Decimal("4.00"),
        ),
    ),
    "deepseek-v4-pro": ModelPricing(
        peak=TokenRates(
            input_cache_miss_per_million=Decimal("9.00"),
            input_cache_hit_per_million=Decimal("0.30"),
            output_per_million=Decimal("27.00"),
        ),
        off_peak=TokenRates(
            input_cache_miss_per_million=Decimal("4.50"),
            input_cache_hit_per_million=Decimal("0.15"),
            output_per_million=Decimal("13.50"),
        ),
    ),
}

MODEL_ALIASES: dict[str, str] = {
    "deepseek-v4-flash": "deepseek-flash",
    "deepseek-v4-flash-vision-exp": "deepseek-flash",
}

# 按国务院当年节假日安排人工维护，例如 date(2026, 10, 1)。
CN_HOLIDAYS: frozenset[date] = frozenset()


def resolve_pricing(model_name: str) -> ModelPricing | None:
    normalized = model_name.strip().lower()
    canonical = MODEL_ALIASES.get(normalized, normalized)
    return MODEL_PRICING.get(canonical)


def is_peak_time(at: datetime | None = None) -> bool:
    local = at or datetime.now(BEIJING_TIMEZONE)
    if local.tzinfo is None:
        local = local.replace(tzinfo=BEIJING_TIMEZONE)
    else:
        local = local.astimezone(BEIJING_TIMEZONE)
    if local.weekday() >= 5 or local.date() in CN_HOLIDAYS:
        return False
    current = local.time().replace(tzinfo=None)
    return time(9, 0) <= current < time(12, 0) or time(14, 0) <= current < time(18, 0)


def calculate_cost_cny(
    model_name: str,
    *,
    input_tokens: int,
    cached_input_tokens: int,
    output_tokens: int,
    at: datetime | None = None,
) -> Decimal:
    """计算一次模型调用的人民币成本；未知模型返回 0。"""
    pricing = resolve_pricing(model_name)
    if pricing is None:
        return Decimal("0")
    rates = pricing.peak if is_peak_time(at) else pricing.off_peak
    total_input = max(0, input_tokens)
    cached_input = min(max(0, cached_input_tokens), total_input)
    uncached_input = total_input - cached_input
    cost = (
        Decimal(uncached_input) * rates.input_cache_miss_per_million
        + Decimal(cached_input) * rates.input_cache_hit_per_million
        + Decimal(max(0, output_tokens)) * rates.output_per_million
    ) / TOKENS_PER_PRICE_UNIT
    return cost.quantize(_COST_QUANTUM, rounding=ROUND_HALF_UP)
