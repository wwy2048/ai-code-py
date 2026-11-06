from datetime import datetime
from decimal import Decimal

from app.model_billing import BEIJING_TIMEZONE, calculate_cost_cny, is_peak_time


def test_flash_cost_uses_cache_hit_miss_and_output_rates() -> None:
    saturday = datetime(2026, 11, 7, 10, 0, tzinfo=BEIJING_TIMEZONE)
    cost = calculate_cost_cny(
        "deepseek-flash",
        input_tokens=1_000_000,
        cached_input_tokens=200_000,
        output_tokens=100_000,
        at=saturday,
    )

    assert cost == Decimal("1.20400000")


def test_peak_time_cost_and_alias() -> None:
    monday_morning = datetime(2026, 11, 9, 10, 0, tzinfo=BEIJING_TIMEZONE)
    cost = calculate_cost_cny(
        "deepseek-v4-flash",
        input_tokens=1_000_000,
        cached_input_tokens=200_000,
        output_tokens=100_000,
        at=monday_morning,
    )

    assert is_peak_time(monday_morning) is True
    assert cost == Decimal("2.40800000")


def test_off_peak_boundaries_and_unknown_model() -> None:
    monday_morning = datetime(2026, 11, 9, 9, 0, tzinfo=BEIJING_TIMEZONE)
    monday_lunch = datetime(2026, 11, 9, 12, 0, tzinfo=BEIJING_TIMEZONE)
    monday_afternoon = datetime(2026, 11, 9, 14, 0, tzinfo=BEIJING_TIMEZONE)
    monday_evening = datetime(2026, 11, 9, 18, 0, tzinfo=BEIJING_TIMEZONE)

    assert is_peak_time(monday_morning) is True
    assert is_peak_time(monday_lunch) is False
    assert is_peak_time(monday_afternoon) is True
    assert is_peak_time(monday_evening) is False
    assert (
        calculate_cost_cny(
            "unknown-model",
            input_tokens=1_000_000,
            cached_input_tokens=1_000_000,
            output_tokens=1_000_000,
            at=monday_morning,
        )
        == Decimal("0")
    )


def test_cached_tokens_are_clamped_to_total_input() -> None:
    saturday = datetime(2026, 11, 7, 10, 0, tzinfo=BEIJING_TIMEZONE)
    cost = calculate_cost_cny(
        "deepseek-flash",
        input_tokens=100,
        cached_input_tokens=200,
        output_tokens=0,
        at=saturday,
    )

    assert cost == Decimal("0.00000200")
