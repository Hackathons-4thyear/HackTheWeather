"""Unit tests for the spray-window advisor.

Boundaries matter as much as they do for Hutton: the wind band is inclusive at
both ends, the rain trace threshold is exclusive, and a window must have the
FULL required dry spell ahead of it - a truncated forecast is not a promise.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import config  # noqa: E402
from services import spray_window as sw  # noqa: E402

NOW = pd.Timestamp("2025-10-01 00:00:00")


def make_forecast(
    hours: int = 48,
    *,
    wind: float | list[float] = 2.5,
    rain: float | list[float] = 0.0,
    start: pd.Timestamp = NOW,
) -> pd.DataFrame:
    """Hourly forecast frame. `wind`/`rain` may be a scalar or a per-hour list."""
    winds = [wind] * hours if isinstance(wind, (int, float)) else list(wind)
    rains = [rain] * hours if isinstance(rain, (int, float)) else list(rain)
    assert len(winds) == hours and len(rains) == hours
    return pd.DataFrame({
        "timestamp": [start + pd.Timedelta(hours=h) for h in range(hours)],
        "rain_mm": rains,
        "wind_speed_ms": winds,
        "temperature_c": [20.0] * hours,
        "humidity_pct": [70.0] * hours,
    })


# --------------------------------------------------------------------------
# Happy path
# --------------------------------------------------------------------------

def test_calm_dry_forecast_yields_a_window():
    advice = sw.find_windows(make_forecast(), now=NOW)
    assert advice.has_window
    assert advice.best is not None
    assert advice.best.duration_hours >= config.SPRAY_WINDOW_MIN_HOURS


def test_window_carries_plain_language_reasons():
    advice = sw.find_windows(make_forecast(), now=NOW)
    reasons = advice.best.reasons
    assert reasons
    assert any("No rain expected" in r for r in reasons)
    assert any("Wind" in r for r in reasons)


# --------------------------------------------------------------------------
# Wind band boundaries - inclusive at BOTH ends
# --------------------------------------------------------------------------

def test_wind_exactly_at_lower_bound_is_allowed():
    advice = sw.find_windows(make_forecast(wind=config.SPRAY_WIND_MIN_MS), now=NOW)
    assert advice.has_window, "wind exactly at the minimum must be sprayable"


def test_wind_exactly_at_upper_bound_is_allowed():
    advice = sw.find_windows(make_forecast(wind=config.SPRAY_WIND_MAX_MS), now=NOW)
    assert advice.has_window, "wind exactly at the maximum must be sprayable"


def test_wind_just_below_lower_bound_is_rejected_as_too_calm():
    advice = sw.find_windows(
        make_forecast(wind=config.SPRAY_WIND_MIN_MS - 0.01), now=NOW)
    assert not advice.has_window
    assert "too calm" in advice.rejection_counts


def test_wind_just_above_upper_bound_is_rejected_as_too_windy():
    advice = sw.find_windows(
        make_forecast(wind=config.SPRAY_WIND_MAX_MS + 0.01), now=NOW)
    assert not advice.has_window
    assert "too windy" in advice.rejection_counts


def test_dead_calm_is_rejected():
    """Calm air is a real hazard - droplets hang and drift unpredictably."""
    advice = sw.find_windows(make_forecast(wind=0.0), now=NOW)
    assert not advice.has_window
    assert advice.rejection_counts.get("too calm", 0) > 0


# --------------------------------------------------------------------------
# Rain rules
# --------------------------------------------------------------------------

def test_rain_during_the_hour_blocks_it():
    rain = [0.0] * 48
    for h in range(10, 14):
        rain[h] = 2.0
    advice = sw.find_windows(make_forecast(rain=rain), now=NOW)
    for w in advice.windows:
        for h in range(10, 14):
            wet_hour = NOW + pd.Timedelta(hours=h)
            assert not (w.start <= wet_hour <= w.end), "window overlaps rain"


def test_trace_rain_does_not_block():
    """0.2 mm is gauge noise, not rain - it must not veto an otherwise good hour."""
    advice = sw.find_windows(
        make_forecast(rain=config.SPRAY_RAIN_TRACE_MM), now=NOW)
    assert advice.has_window


def test_just_above_trace_rain_does_block():
    advice = sw.find_windows(
        make_forecast(rain=config.SPRAY_RAIN_TRACE_MM + 0.01), now=NOW)
    assert not advice.has_window


def test_rain_within_required_dry_hours_blocks_the_earlier_hour():
    """Rain at hour 6 must veto hour 0, which needs 6 dry hours after it."""
    rain = [0.0] * 48
    rain[config.SPRAY_DRY_HOURS_REQUIRED] = 5.0
    advice = sw.find_windows(make_forecast(rain=rain), now=NOW)
    first_hour = NOW
    for w in advice.windows:
        assert not (w.start <= first_hour <= w.end), \
            "hour 0 should be vetoed by rain inside its dry requirement"


def test_all_day_rain_yields_no_window_with_an_explanation():
    advice = sw.find_windows(make_forecast(rain=10.0), now=NOW)
    assert not advice.has_window
    assert advice.reasons
    assert any("No spray window" in r for r in advice.reasons)
    assert any("Main blockers" in r for r in advice.reasons)


# --------------------------------------------------------------------------
# Truncated forecast - never promise what we cannot see
# --------------------------------------------------------------------------

def test_truncated_forecast_cannot_confirm_a_dry_spell():
    """With only 4 hours of forecast we cannot confirm 6 dry hours ahead."""
    advice = sw.find_windows(make_forecast(hours=4), now=NOW)
    assert not advice.has_window
    assert "forecast ends too soon to confirm" in advice.rejection_counts


def test_last_hours_of_forecast_are_not_offered_as_windows():
    df = make_forecast(hours=24)
    advice = sw.find_windows(df, now=NOW)
    horizon = df["timestamp"].iloc[-1]
    for w in advice.windows:
        remaining = (horizon - w.end) / pd.Timedelta(hours=1)
        assert remaining >= config.SPRAY_DRY_HOURS_REQUIRED


# --------------------------------------------------------------------------
# Window length
# --------------------------------------------------------------------------

def test_single_sprayable_hour_is_too_short_to_offer():
    """One isolated good hour is not worth mixing a tank for."""
    wind = [10.0] * 48
    wind[20] = 2.5  # exactly one good hour
    advice = sw.find_windows(make_forecast(wind=wind), now=NOW)
    assert not advice.has_window


def test_two_consecutive_sprayable_hours_meet_the_minimum():
    wind = [10.0] * 48
    wind[20] = 2.5
    wind[21] = 2.5
    advice = sw.find_windows(make_forecast(wind=wind), now=NOW)
    assert advice.has_window
    assert advice.best.duration_hours == 2


# --------------------------------------------------------------------------
# Live station override
# --------------------------------------------------------------------------

def test_currently_raining_rules_out_the_first_hour():
    advice = sw.find_windows(make_forecast(), now=NOW, currently_raining=True)
    assert advice.rejection_counts.get("raining now") == 1
    for w in advice.windows:
        assert w.start > NOW


def test_is_raining_now_reads_the_latest_observation():
    obs = pd.DataFrame({
        "timestamp": [NOW - pd.Timedelta(hours=1), NOW],
        "rain_mm": [0.0, 3.0],
    })
    assert sw.is_raining_now(obs) is True

    obs.loc[1, "rain_mm"] = 0.0
    assert sw.is_raining_now(obs) is False


def test_is_raining_now_returns_none_when_unknown():
    """'We do not know' must not be reported as 'it is dry'."""
    assert sw.is_raining_now(pd.DataFrame()) is None
    assert sw.is_raining_now(pd.DataFrame({"timestamp": [NOW]})) is None
    nan_obs = pd.DataFrame({"timestamp": [NOW], "rain_mm": [float("nan")]})
    assert sw.is_raining_now(nan_obs) is None


# --------------------------------------------------------------------------
# Ranking
# --------------------------------------------------------------------------

def test_morning_window_outranks_a_midday_one():
    """Both sprayable, but early morning loses less to evaporation."""
    wind = [10.0] * 48
    for h in range(6, 11):       # 06:00-10:00, preferred
        wind[h] = 2.5
    for h in range(11, 15):      # 11:00-14:00, hot part of the day
        wind[h] = 2.5
    advice = sw.find_windows(make_forecast(wind=wind), now=NOW)
    assert advice.has_window
    assert advice.best.start.hour == 6, "the morning window should rank first"


def test_windows_are_sorted_by_score_descending():
    wind = [10.0] * 48
    for h in list(range(6, 11)) + list(range(12, 15)):
        wind[h] = 2.5
    advice = sw.find_windows(make_forecast(wind=wind), now=NOW)
    scores = [w.score for w in advice.windows]
    assert scores == sorted(scores, reverse=True)


def test_window_label_is_human_readable():
    advice = sw.find_windows(make_forecast(), now=NOW)
    label = advice.best.label
    assert ":" in label and "-" in label


# --------------------------------------------------------------------------
# Degenerate input - must never raise
# --------------------------------------------------------------------------

def test_empty_forecast_returns_advice_not_an_exception():
    advice = sw.find_windows(pd.DataFrame(), now=NOW)
    assert not advice.has_window
    assert advice.reasons


def test_missing_wind_column_is_explained():
    df = make_forecast().drop(columns=["wind_speed_ms"])
    advice = sw.find_windows(df, now=NOW)
    assert not advice.has_window
    assert any("wind_speed_ms" in r for r in advice.reasons)


def test_nan_wind_is_rejected_not_crashed():
    wind = [2.5] * 48
    wind[5] = float("nan")
    advice = sw.find_windows(make_forecast(wind=wind), now=NOW)
    assert "no wind reading" in advice.rejection_counts


def test_forecast_entirely_in_the_past_yields_nothing():
    old = make_forecast(start=NOW - pd.Timedelta(days=10))
    advice = sw.find_windows(old, now=NOW)
    assert not advice.has_window
    assert any("does not cover" in r for r in advice.reasons)
