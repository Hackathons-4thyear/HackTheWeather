"""Unit tests for the Hutton criteria and the humid-hours fallback.

The boundary cases matter most: the criteria are defined with >= comparisons, so
exactly 10.0 degC, exactly 90% RH and exactly 6 humid hours must all COUNT. An
off-by-one here means a farmer is not warned on the day it matters.
"""

from __future__ import annotations

import sys
from datetime import date
from pathlib import Path

import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import config  # noqa: E402
from services import disease_engine as de  # noqa: E402


# --------------------------------------------------------------------------
# Builders
# --------------------------------------------------------------------------

def make_day(
    day: str,
    *,
    humid_hours: int = 0,
    min_temp: float = 15.0,
    base_temp: float = 20.0,
    humid_rh: float = 95.0,
    dry_rh: float = 70.0,
    drop_hours: list[int] | None = None,
) -> pd.DataFrame:
    """One day of hourly canonical data with an exact number of humid hours.

    `humid_hours` hours are set to `humid_rh`; the rest to `dry_rh`.
    Hour 3 carries `min_temp` so the daily minimum is exactly controllable.
    `drop_hours` removes those hours entirely, to simulate sensor dropouts.
    """
    drop_hours = drop_hours or []
    rows = []
    for h in range(24):
        if h in drop_hours:
            continue
        rows.append({
            "timestamp": pd.Timestamp(f"{day} {h:02d}:00:00"),
            "temperature_c": min_temp if h == 3 else base_temp,
            "humidity_pct": humid_rh if h < humid_hours else dry_rh,
            "rain_mm": 0.0,
            "wind_speed_ms": 2.0,
        })
    return pd.DataFrame(rows)


def make_days(specs: list[dict]) -> pd.DataFrame:
    """Concatenate several make_day() frames. Each spec needs a 'day' key."""
    return pd.concat([make_day(**s) for s in specs], ignore_index=True)


# --------------------------------------------------------------------------
# Hutton boundaries - humid hours
# --------------------------------------------------------------------------

def test_exactly_six_humid_hours_is_a_hutton_day():
    """>= 6 hours, so exactly 6 MUST count."""
    df = make_day("2025-10-01", humid_hours=6, min_temp=15.0)
    day = de.evaluate_days(df)[0]
    assert day.humid_hours == 6.0
    assert day.is_hutton_day is True


def test_five_humid_hours_is_not_a_hutton_day():
    df = make_day("2025-10-01", humid_hours=5, min_temp=15.0)
    day = de.evaluate_days(df)[0]
    assert day.humid_hours == 5.0
    assert day.is_hutton_day is False


def test_zero_humid_hours_is_not_a_hutton_day():
    df = make_day("2025-10-01", humid_hours=0, min_temp=20.0)
    day = de.evaluate_days(df)[0]
    assert day.is_hutton_day is False


# --------------------------------------------------------------------------
# Hutton boundaries - temperature
# --------------------------------------------------------------------------

def test_exactly_ten_degrees_minimum_is_a_hutton_day():
    """>= 10.0 degC, so exactly 10.0 MUST count."""
    df = make_day("2025-10-01", humid_hours=8, min_temp=10.0)
    day = de.evaluate_days(df)[0]
    assert day.min_temp_c == pytest.approx(10.0)
    assert day.met_temp_criterion is True
    assert day.is_hutton_day is True


def test_just_below_ten_degrees_is_not_a_hutton_day():
    df = make_day("2025-10-01", humid_hours=8, min_temp=9.9)
    day = de.evaluate_days(df)[0]
    assert day.met_temp_criterion is False
    assert day.is_hutton_day is False


def test_cold_night_blocks_hutton_despite_long_humid_spell():
    """A 20-hour humid spell still fails if the night dropped below 10 degC."""
    df = make_day("2025-10-01", humid_hours=20, min_temp=4.0)
    day = de.evaluate_days(df)[0]
    assert day.met_humidity_criterion is True
    assert day.met_temp_criterion is False
    assert day.is_hutton_day is False


# --------------------------------------------------------------------------
# Hutton boundaries - the RH threshold itself
# --------------------------------------------------------------------------

def test_exactly_ninety_percent_rh_counts_as_humid():
    """>= 90%, so a reading of exactly 90.0 MUST count toward humid hours."""
    df = make_day("2025-10-01", humid_hours=7, humid_rh=90.0, min_temp=15.0)
    day = de.evaluate_days(df)[0]
    assert day.humid_hours == 7.0
    assert day.is_hutton_day is True


def test_just_below_ninety_percent_does_not_count():
    df = make_day("2025-10-01", humid_hours=7, humid_rh=89.9, min_temp=15.0)
    day = de.evaluate_days(df)[0]
    assert day.humid_hours == 0.0
    assert day.is_hutton_day is False


# --------------------------------------------------------------------------
# Consecutive days
# --------------------------------------------------------------------------

def test_two_consecutive_hutton_days_give_high_risk():
    df = make_days([
        {"day": "2025-10-01", "humid_hours": 8, "min_temp": 14.0},
        {"day": "2025-10-02", "humid_hours": 9, "min_temp": 13.0},
    ])
    result = de.assess(df)
    assert result.consecutive_hutton_days == 2
    assert result.hutton_level == "HIGH"
    assert result.level == "HIGH"
    assert any("2 Hutton days in a row" in r for r in result.reasons)


def test_one_hutton_day_is_moderate_not_high():
    df = make_days([
        {"day": "2025-10-01", "humid_hours": 2, "min_temp": 14.0},
        {"day": "2025-10-02", "humid_hours": 8, "min_temp": 13.0},
    ])
    result = de.assess(df)
    assert result.consecutive_hutton_days == 1
    assert result.hutton_level == "MODERATE"


def test_non_consecutive_hutton_days_do_not_give_high_risk():
    """Hutton, then a clear day, then Hutton: the run is broken, so NOT HIGH."""
    df = make_days([
        {"day": "2025-10-01", "humid_hours": 8, "min_temp": 14.0},   # Hutton
        {"day": "2025-10-02", "humid_hours": 1, "min_temp": 14.0},   # not
        {"day": "2025-10-03", "humid_hours": 8, "min_temp": 14.0},   # Hutton
    ])
    result = de.assess(df)
    assert result.consecutive_hutton_days == 1
    assert result.hutton_level == "MODERATE"
    assert result.hutton_level != "HIGH"


def test_three_consecutive_hutton_days_still_high():
    df = make_days([
        {"day": "2025-10-01", "humid_hours": 8, "min_temp": 14.0},
        {"day": "2025-10-02", "humid_hours": 8, "min_temp": 14.0},
        {"day": "2025-10-03", "humid_hours": 8, "min_temp": 14.0},
    ])
    result = de.assess(df)
    assert result.consecutive_hutton_days == 3
    assert result.hutton_level == "HIGH"


def test_run_counts_only_days_ending_at_the_most_recent():
    """Two Hutton days followed by a clear day: the run ending today is 0."""
    df = make_days([
        {"day": "2025-10-01", "humid_hours": 8, "min_temp": 14.0},
        {"day": "2025-10-02", "humid_hours": 8, "min_temp": 14.0},
        {"day": "2025-10-03", "humid_hours": 0, "min_temp": 14.0},
    ])
    result = de.assess(df)
    assert result.consecutive_hutton_days == 0
    # Still flagged MODERATE because it happened within the last 3 days.
    assert result.hutton_level == "MODERATE"


# --------------------------------------------------------------------------
# Data gaps - the honesty requirement
# --------------------------------------------------------------------------

def test_day_missing_more_than_25_percent_is_insufficient():
    """7 of 24 hours missing = 29% > 25%, so the day cannot be judged."""
    df = make_day("2025-10-01", humid_hours=8, min_temp=14.0,
                  drop_hours=list(range(7)))
    day = de.evaluate_days(df)[0]
    assert day.hours_observed == 17
    assert day.missing_fraction > de.MAX_MISSING_FRACTION
    assert day.sufficient_data is False
    assert day.is_hutton_day is None
    assert any("Insufficient data" in r for r in day.reasons)


def test_day_missing_exactly_25_percent_is_still_judged():
    """6 of 24 hours missing = exactly 25%, which is the limit, so still OK."""
    df = make_day("2025-10-01", humid_hours=8, min_temp=14.0,
                  drop_hours=list(range(6)))
    day = de.evaluate_days(df)[0]
    assert day.hours_observed == 18
    assert day.missing_fraction == pytest.approx(0.25)
    assert day.sufficient_data is True
    assert day.is_hutton_day is not None


def test_insufficient_day_breaks_the_consecutive_run():
    """We must not bridge a data gap and claim two consecutive Hutton days."""
    df = make_days([
        {"day": "2025-10-01", "humid_hours": 8, "min_temp": 14.0},
        {"day": "2025-10-02", "humid_hours": 8, "min_temp": 14.0,
         "drop_hours": list(range(12))},  # 50% missing -> unjudgeable
        {"day": "2025-10-03", "humid_hours": 8, "min_temp": 14.0},
    ])
    result = de.assess(df)
    # The run ending on the last day is 1, not 3 - the gap broke it.
    assert result.consecutive_hutton_days == 1
    assert result.hutton_level != "HIGH"


def test_insufficient_days_are_reported_in_reasons_and_warnings():
    df = make_days([
        {"day": "2025-10-01", "humid_hours": 8, "min_temp": 14.0,
         "drop_hours": list(range(15))},
        {"day": "2025-10-02", "humid_hours": 8, "min_temp": 14.0},
    ])
    result = de.assess(df)
    assert result.data_warnings
    assert any("could not be judged" in r for r in result.reasons)


def test_missing_hours_are_noted_even_when_day_is_judged():
    df = make_day("2025-10-01", humid_hours=8, min_temp=14.0, drop_hours=[0, 1])
    day = de.evaluate_days(df)[0]
    assert day.sufficient_data is True
    assert any("22 of 24 hours" in r for r in day.reasons)


# --------------------------------------------------------------------------
# Humid-hours fallback
# --------------------------------------------------------------------------

def test_humid_hours_bands():
    """LOW below 4h, MODERATE at 4h, HIGH at 6h - boundaries included."""
    for hours, expected in [(0, "LOW"), (3, "LOW"), (4, "MODERATE"),
                            (5, "MODERATE"), (6, "HIGH"), (12, "HIGH")]:
        df = make_day("2025-10-01", humid_hours=hours, min_temp=15.0)
        level, counted, _ = de.humid_hours_risk(df)
        assert counted == float(hours), f"{hours}h counted as {counted}"
        assert level == expected, f"{hours} humid hours -> {level}, expected {expected}"


def test_humid_hours_temperature_gate():
    """Humid but cold is not a blight risk - the gate must hold it at LOW."""
    df = make_day("2025-10-01", humid_hours=20, min_temp=2.0, base_temp=5.0)
    level, hours, reasons = de.humid_hours_risk(df)
    assert hours == 20.0
    assert level == "LOW"
    assert any("below the" in r for r in reasons)


def test_humid_hours_works_on_a_partial_day():
    """The fallback must answer mid-day, when Hutton cannot."""
    df = make_day("2025-10-01", humid_hours=6, min_temp=15.0,
                  drop_hours=list(range(10, 24)))
    level, hours, reasons = de.humid_hours_risk(df)
    assert level == "HIGH"
    assert any("only 10 of the last 24 hours" in r for r in reasons)


# --------------------------------------------------------------------------
# Explainability
# --------------------------------------------------------------------------

def test_every_assessment_carries_reasons():
    df = make_day("2025-10-01", humid_hours=8, min_temp=14.0)
    result = de.assess(df)
    assert result.reasons
    assert all(isinstance(r, str) and r.strip() for r in result.reasons)


def test_reasons_quote_the_actual_measured_values():
    df = make_day("2025-10-01", humid_hours=8, min_temp=12.3)
    day = de.evaluate_days(df)[0]
    joined = " ".join(day.reasons)
    assert "12.3C" in joined
    assert "8 hours" in joined


def test_overnight_humid_spell_is_described_as_overnight():
    """Humidity in hours 0-7 should read as 'overnight', not a bare number."""
    df = make_day("2025-10-01", humid_hours=8, min_temp=14.0)
    day = de.evaluate_days(df)[0]
    assert any("overnight" in r for r in day.reasons)


# --------------------------------------------------------------------------
# Degenerate input - must never raise
# --------------------------------------------------------------------------

def test_empty_frame_returns_unknown_not_an_exception():
    result = de.assess(pd.DataFrame())
    assert result.level == de.UNKNOWN
    assert result.is_actionable is False
    assert result.reasons


def test_missing_humidity_column_returns_unknown():
    df = make_day("2025-10-01", humid_hours=8).drop(columns=["humidity_pct"])
    result = de.assess(df)
    assert result.level == de.UNKNOWN
    assert any("humidity_pct" in r for r in result.reasons)


def test_all_nan_humidity_returns_unknown():
    df = make_day("2025-10-01", humid_hours=8)
    df["humidity_pct"] = float("nan")
    result = de.assess(df)
    assert result.level == de.UNKNOWN


def test_fifteen_minute_data_is_resampled_to_hourly():
    """Station data arrives every 15 min; Hutton is defined on hourly readings."""
    rows = []
    for h in range(24):
        for q in range(4):
            rows.append({
                "timestamp": pd.Timestamp(f"2025-10-01 {h:02d}:{q * 15:02d}:00"),
                "temperature_c": 14.0,
                "humidity_pct": 95.0 if h < 8 else 60.0,
                "rain_mm": 0.0,
                "wind_speed_ms": 2.0,
            })
    df = pd.DataFrame(rows)
    day = de.evaluate_days(df)[0]
    # 8 humid HOURS, not 32 humid quarter-hours.
    assert day.humid_hours == 8.0
    assert day.hours_observed == 24
    assert day.is_hutton_day is True


def test_risk_assessment_satisfies_the_ml_swap_protocol():
    """The rule engine must match the contract an ML model would implement."""
    class DummyModel:
        def assess(self, df: pd.DataFrame) -> de.RiskAssessment:
            return de.assess(df)

    assert isinstance(DummyModel(), de.RiskModel)
    out = DummyModel().assess(make_day("2025-10-01", humid_hours=8, min_temp=14.0))
    assert isinstance(out, de.RiskAssessment)
    assert out.level in (*config.RISK_LEVELS, de.UNKNOWN)


# --------------------------------------------------------------------------
# How the two measures combine
# --------------------------------------------------------------------------

def test_high_is_reserved_for_the_official_hutton_trigger():
    """Humid hours alone must NEVER produce HIGH.

    Six humid hours in a rolling day is not the same evidence as two
    consecutive qualifying days. If the app says HIGH, Hutton fired.
    """
    # 8 humid hours in the last 24h, but the cold night blocks any Hutton day.
    df = make_days([
        {"day": "2025-10-01", "humid_hours": 0, "min_temp": 5.0, "base_temp": 12.0},
        {"day": "2025-10-02", "humid_hours": 8, "min_temp": 5.0, "base_temp": 12.0},
    ])
    result = de.assess(df)
    assert result.humid_hours_level == "HIGH"
    assert result.hutton_level == "LOW"
    assert result.level == "MODERATE", "humid hours must cap at MODERATE"
    assert result.level != "HIGH"


def test_hutton_moderate_is_not_overridden_by_humid_hours():
    """One Hutton day stays MODERATE even when humid hours reads HIGH."""
    df = make_days([
        {"day": "2025-10-01", "humid_hours": 2, "min_temp": 15.0},
        {"day": "2025-10-02", "humid_hours": 7, "min_temp": 14.0},
    ])
    result = de.assess(df)
    assert result.hutton_level == "MODERATE"
    assert result.humid_hours_level == "HIGH"
    assert result.level == "MODERATE"
    assert result.method == "hutton"


def test_humid_hours_escalates_low_to_moderate():
    """A building humid spell must not be reported as plain LOW."""
    df = make_days([
        {"day": "2025-10-01", "humid_hours": 0, "min_temp": 5.0, "base_temp": 12.0},
        {"day": "2025-10-02", "humid_hours": 8, "min_temp": 5.0, "base_temp": 12.0},
    ])
    result = de.assess(df)
    assert result.method == "humid_hours_escalation"
    assert any("Watch closely" in r for r in result.reasons)


def test_humid_hours_used_when_hutton_cannot_be_computed():
    """Every day unjudgeable -> fall back to the rolling humid-hours measure."""
    df = make_days([
        {"day": "2025-10-01", "humid_hours": 8, "min_temp": 14.0,
         "drop_hours": list(range(16))},
    ])
    result = de.assess(df)
    assert result.hutton_level == de.UNKNOWN
    assert result.method == "humid_hours"
    assert result.level == result.humid_hours_level


def test_both_measures_are_always_reported():
    """The UI shows both, so both must be populated regardless of which drove."""
    df = make_days([
        {"day": "2025-10-01", "humid_hours": 8, "min_temp": 14.0},
        {"day": "2025-10-02", "humid_hours": 9, "min_temp": 14.0},
    ])
    result = de.assess(df)
    assert result.hutton_level in (*config.RISK_LEVELS, de.UNKNOWN)
    assert result.humid_hours_level in (*config.RISK_LEVELS, de.UNKNOWN)
    assert result.level == "HIGH" and result.method == "hutton"


# --------------------------------------------------------------------------
# Coverage affects the humid-hours measure ASYMMETRICALLY
# --------------------------------------------------------------------------

def test_positive_humid_count_stands_despite_poor_coverage():
    """Observing 6 humid hours proves 6 happened, however many we missed."""
    df = make_day("2025-10-01", humid_hours=6, min_temp=15.0,
                  drop_hours=list(range(10, 24)))
    level, hours, reasons = de.humid_hours_risk(df)
    assert hours == 6.0
    assert level == "HIGH", "a proven count must not be downgraded by gaps"


def test_low_verdict_requires_real_coverage():
    """Zero humid hours in 8 of 24 proves nothing about the other 16.

    LOW is the only verdict that tells a farmer to relax, so it must not be
    issued from a mostly-unobserved window - an overnight spell hides exactly
    there.
    """
    df = make_day("2025-10-01", humid_hours=0, min_temp=15.0,
                  drop_hours=list(range(8, 24)))
    level, hours, reasons = de.humid_hours_risk(df)
    assert hours == 0.0
    assert level == de.UNKNOWN
    assert any("Too little to call it safe" in r for r in reasons)


def test_low_verdict_allowed_with_good_coverage():
    df = make_day("2025-10-01", humid_hours=0, min_temp=15.0,
                  drop_hours=list(range(22, 24)))
    level, _, _ = de.humid_hours_risk(df)
    assert level == "LOW"


def test_rolling_window_selects_by_time_not_row_count():
    """A gap must not let stale readings pose as 'the last 24 hours'.

    Day 1 is humid, then a four-day gap, then a dry day. Selecting the last 24
    ROWS would reach back into day 1 and report its humid hours as current.
    """
    df = make_days([
        {"day": "2025-10-01", "humid_hours": 12, "min_temp": 15.0},
        {"day": "2025-10-06", "humid_hours": 0, "min_temp": 15.0},
    ])
    level, hours, _ = de.humid_hours_risk(df)
    assert hours == 0.0, "humid hours from five days ago must not leak in"
    assert level == "LOW"


def test_insufficient_data_produces_an_unknown_assessment():
    """End to end: a mostly-missing day must not report a confident LOW."""
    df = make_days([
        {"day": "2025-10-01", "humid_hours": 0, "min_temp": 15.0,
         "drop_hours": list(range(8, 24))},
    ])
    result = de.assess(df)
    assert result.level == de.UNKNOWN
    assert result.is_actionable is False
