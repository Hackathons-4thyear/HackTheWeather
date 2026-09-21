"""Unit tests for the spray-window advisor.

Boundaries matter as much as they do for Hutton: the wind band is inclusive at
both ends, the rain trace and wet-leaf thresholds are exclusive/inclusive
respectively, a window must have the FULL required dry spell ahead of it, and
spraying happens in daylight while the dry-spell check looks through the night.

Window bounds: `start` is inclusive, `end` is EXCLUSIVE. A window covering
09:00 through 16:59 is start=09:00, end=17:00, duration_hours=8.0.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import config  # noqa: E402
from services import spray_window as sw  # noqa: E402

NOW = pd.Timestamp("2025-10-01 00:00:00")   # a Wednesday, midnight
HOUR = pd.Timedelta(hours=1)


def make_forecast(
    hours: int = 48,
    *,
    wind: float | list[float] = 2.5,
    rain: float | list[float] = 0.0,
    humidity: float | list[float] = 70.0,
    start: pd.Timestamp = NOW,
) -> pd.DataFrame:
    """Hourly forecast frame. Each field may be a scalar or a per-hour list."""
    def expand(v):
        return [v] * hours if isinstance(v, (int, float)) else list(v)

    winds, rains, hums = expand(wind), expand(rain), expand(humidity)
    assert len(winds) == len(rains) == len(hums) == hours
    return pd.DataFrame({
        "timestamp": [start + h * HOUR for h in range(hours)],
        "rain_mm": rains,
        "wind_speed_ms": winds,
        "humidity_pct": hums,
        "temperature_c": [20.0] * hours,
    })


def sprayable_only(indices: list[int], hours: int = 48) -> list[float]:
    """Wind list where only `indices` sit inside the usable band."""
    wind = [10.0] * hours          # too windy everywhere else
    for i in indices:
        wind[i] = 2.5
    return wind


# --------------------------------------------------------------------------
# Happy path
# --------------------------------------------------------------------------

def test_calm_dry_forecast_yields_a_window():
    advice = sw.find_windows(make_forecast(), now=NOW)
    assert advice.has_window
    assert advice.best.duration_hours >= config.SPRAY_MIN_WINDOW_HOURS


def test_window_carries_plain_language_reasons():
    advice = sw.find_windows(make_forecast(), now=NOW)
    reasons = advice.best.reasons
    assert any("No rain expected" in r for r in reasons)
    assert any("Wind" in r for r in reasons)
    assert any("Humidity" in r for r in reasons)


def test_all_windows_lie_inside_daylight():
    advice = sw.find_windows(make_forecast(), now=NOW)
    assert advice.has_window
    for w in advice.windows:
        d_start, d_end = sw._daylight_bounds(w.start)
        assert w.start >= d_start
        assert w.end <= d_end


# --------------------------------------------------------------------------
# 1. Daylight clipping
# --------------------------------------------------------------------------

def test_overnight_run_is_clipped_to_its_morning_tail():
    """The case from review: Mon 19:00 - Tue 09:00 must become Tue 06:30-09:00.

    Clipped, not discarded - the morning part is perfectly sprayable.
    """
    # Sprayable from 19:00 on day 1 through 08:00 on day 2 (indices 19..32).
    advice = sw.find_windows(
        make_forecast(wind=sprayable_only(list(range(19, 33)))), now=NOW)
    assert advice.has_window, "the morning tail should survive clipping"
    w = advice.best
    assert w.start == pd.Timestamp("2025-10-02 06:30:00")
    assert w.end == pd.Timestamp("2025-10-02 09:00:00")
    assert w.duration_hours == pytest.approx(2.5)
    assert w.was_clipped is True
    assert any("Trimmed to daylight" in r for r in w.reasons)


def test_clipping_records_the_night_hours_as_rejected():
    advice = sw.find_windows(
        make_forecast(wind=sprayable_only(list(range(19, 33)))), now=NOW)
    assert advice.rejection_counts.get(sw.R_NIGHT, 0) > 0


def test_window_entirely_at_night_is_rejected():
    """20:00-23:59 has no daylight overlap at all, so nothing survives."""
    advice = sw.find_windows(
        make_forecast(wind=sprayable_only([20, 21, 22, 23])), now=NOW)
    assert not advice.has_window
    assert advice.rejection_counts.get(sw.R_NIGHT, 0) >= 4


def test_daylight_boundary_hours_are_trimmed_not_dropped():
    """06:00 and 18:00 partially overlap daylight; they must be trimmed."""
    advice = sw.find_windows(make_forecast(), now=NOW)
    first = min(advice.windows, key=lambda w: w.start)
    assert first.start.hour == config.SPRAY_DAYLIGHT_START.hour
    assert first.start.minute == config.SPRAY_DAYLIGHT_START.minute


def test_full_dry_day_gives_exactly_the_daylight_span():
    advice = sw.find_windows(make_forecast(hours=72), now=NOW)
    full_days = [w for w in advice.windows
                 if w.duration_hours == pytest.approx(12.0)]
    assert full_days, "a fully sprayable day should yield 06:30-18:30 = 12h"
    w = full_days[0]
    assert (w.start.hour, w.start.minute) == (6, 30)
    assert (w.end.hour, w.end.minute) == (18, 30)


# --------------------------------------------------------------------------
# 2. Wet-leaf rule
# --------------------------------------------------------------------------

def test_humidity_exactly_at_threshold_is_rejected():
    """>= the threshold means exactly 90.0 counts as wet leaves."""
    advice = sw.find_windows(
        make_forecast(humidity=config.SPRAY_MAX_HUMIDITY_PCT), now=NOW)
    assert not advice.has_window
    assert advice.rejection_counts.get(sw.R_WET_LEAF, 0) > 0


def test_humidity_just_below_threshold_is_allowed():
    advice = sw.find_windows(
        make_forecast(humidity=config.SPRAY_MAX_HUMIDITY_PCT - 0.1), now=NOW)
    assert advice.has_window
    assert sw.R_WET_LEAF not in advice.rejection_counts


def test_wet_leaf_threshold_is_the_hutton_constant_not_a_copy():
    """The two must never drift apart, so they must be the same object/value."""
    assert config.SPRAY_MAX_HUMIDITY_PCT == config.HUTTON_RH_THRESHOLD_PCT


def test_dewy_morning_pushes_the_window_later():
    """Dew until 09:00 should delay the start, not kill the window."""
    hum = [95.0] * 48
    for h in list(range(9, 19)) + list(range(33, 43)):
        hum[h] = 60.0
    advice = sw.find_windows(make_forecast(humidity=hum), now=NOW)
    assert advice.has_window
    first = min(advice.windows, key=lambda w: w.start)
    assert first.start == pd.Timestamp("2025-10-01 09:00:00"), \
        "window should begin once the dew has burned off"


def test_missing_humidity_is_flagged_not_silently_skipped():
    df = make_forecast().drop(columns=["humidity_pct"])
    advice = sw.find_windows(df, now=NOW)
    assert any("wet leaves could not be checked" in r for r in advice.reasons)


# --------------------------------------------------------------------------
# 3. Minimum window length, applied AFTER clipping
# --------------------------------------------------------------------------

def test_clipped_window_under_two_hours_is_dropped():
    """Mon 19:00 - Tue 08:00 clips to Tue 06:30-08:00 = 1.5h, under the minimum."""
    advice = sw.find_windows(
        make_forecast(wind=sprayable_only(list(range(19, 32)))), now=NOW)
    assert not advice.has_window
    assert advice.rejection_counts.get(sw.R_SHORT, 0) > 0


def test_single_sprayable_hour_is_too_short_to_offer():
    advice = sw.find_windows(make_forecast(wind=sprayable_only([10])), now=NOW)
    assert not advice.has_window
    assert advice.rejection_counts.get(sw.R_SHORT, 0) > 0


def test_two_consecutive_daylight_hours_meet_the_minimum():
    advice = sw.find_windows(make_forecast(wind=sprayable_only([10, 11])), now=NOW)
    assert advice.has_window
    assert advice.best.duration_hours == pytest.approx(2.0)


# --------------------------------------------------------------------------
# 4. The dry-spell check looks THROUGH the night
# --------------------------------------------------------------------------

def test_night_rain_vetoes_an_afternoon_window():
    """Rain at 21:00 must veto daylight hours 15:00-18:00, six hours earlier.

    Only the spraying is daylight-limited; rain at 2am still washes off a
    7pm spray, so the dry requirement ignores the daylight bounds.
    """
    rain = [0.0] * 48
    rain[21] = 5.0  # 21:00, well after dark
    advice = sw.find_windows(make_forecast(rain=rain), now=NOW)
    assert advice.rejection_counts.get(sw.R_RAIN_SOON, 0) > 0
    for w in advice.windows:
        if w.start.date() == NOW.date():
            assert w.end <= pd.Timestamp("2025-10-01 15:00:00"), \
                "afternoon hours should be vetoed by the 21:00 rain"


def test_dry_hours_after_counts_night_hours():
    """dry_hours_after must keep counting past dusk."""
    advice = sw.find_windows(make_forecast(hours=48), now=NOW)
    w = min(advice.windows, key=lambda x: x.start)
    assert w.dry_hours_after > 6, "a dry night should count toward the tally"


# --------------------------------------------------------------------------
# Wind band boundaries - inclusive at BOTH ends
# --------------------------------------------------------------------------

def test_wind_exactly_at_lower_bound_is_allowed():
    advice = sw.find_windows(make_forecast(wind=config.SPRAY_WIND_MIN_MS), now=NOW)
    assert advice.has_window


def test_wind_exactly_at_upper_bound_is_allowed():
    advice = sw.find_windows(make_forecast(wind=config.SPRAY_WIND_MAX_MS), now=NOW)
    assert advice.has_window


def test_wind_just_below_lower_bound_is_rejected_as_too_calm():
    advice = sw.find_windows(
        make_forecast(wind=config.SPRAY_WIND_MIN_MS - 0.01), now=NOW)
    assert not advice.has_window
    assert sw.R_CALM in advice.rejection_counts


def test_wind_just_above_upper_bound_is_rejected_as_too_windy():
    advice = sw.find_windows(
        make_forecast(wind=config.SPRAY_WIND_MAX_MS + 0.01), now=NOW)
    assert not advice.has_window
    assert sw.R_WINDY in advice.rejection_counts


def test_dead_calm_is_rejected():
    advice = sw.find_windows(make_forecast(wind=0.0), now=NOW)
    assert not advice.has_window
    assert advice.rejection_counts.get(sw.R_CALM, 0) > 0


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
            wet = NOW + h * HOUR
            assert not (w.start <= wet < w.end), "window overlaps rain"


def test_trace_rain_does_not_block():
    """0.2 mm is gauge noise, not rain - it must not veto a good hour."""
    advice = sw.find_windows(
        make_forecast(rain=config.SPRAY_RAIN_TRACE_MM), now=NOW)
    assert advice.has_window


def test_just_above_trace_rain_does_block():
    advice = sw.find_windows(
        make_forecast(rain=config.SPRAY_RAIN_TRACE_MM + 0.01), now=NOW)
    assert not advice.has_window


def test_all_day_rain_yields_no_window_with_an_explanation():
    advice = sw.find_windows(make_forecast(rain=10.0), now=NOW)
    assert not advice.has_window
    assert any("No spray window" in r for r in advice.reasons)
    assert any("Main blockers" in r for r in advice.reasons)


# --------------------------------------------------------------------------
# Truncated forecast - never promise what we cannot see
# --------------------------------------------------------------------------

def test_truncated_forecast_cannot_confirm_a_dry_spell():
    """Four daylight hours is not enough to confirm six dry hours ahead."""
    start = pd.Timestamp("2025-10-01 08:00:00")
    advice = sw.find_windows(make_forecast(hours=4, start=start), now=start)
    assert not advice.has_window
    assert sw.R_TRUNCATED in advice.rejection_counts


def test_last_hours_of_forecast_are_not_offered_as_windows():
    df = make_forecast(hours=24)
    advice = sw.find_windows(df, now=NOW)
    horizon_end = df["timestamp"].iloc[-1] + HOUR
    for w in advice.windows:
        assert (horizon_end - w.end) / HOUR >= config.SPRAY_DRY_HOURS_REQUIRED


# --------------------------------------------------------------------------
# Live station override
# --------------------------------------------------------------------------

def test_currently_raining_rules_out_the_first_hour():
    start = pd.Timestamp("2025-10-01 09:00:00")
    advice = sw.find_windows(make_forecast(start=start), now=start,
                             currently_raining=True)
    assert advice.rejection_counts.get(sw.R_RAINING_NOW) == 1
    for w in advice.windows:
        assert w.start >= start + HOUR


def test_is_raining_now_reads_the_latest_observation():
    obs = pd.DataFrame({"timestamp": [NOW - HOUR, NOW], "rain_mm": [0.0, 3.0]})
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
    wind = sprayable_only(list(range(7, 11)) + list(range(12, 16)))
    advice = sw.find_windows(make_forecast(wind=wind), now=NOW)
    assert advice.has_window
    assert advice.best.start.hour == 7, "the morning window should rank first"


def test_windows_are_sorted_by_score_descending():
    wind = sprayable_only(list(range(7, 11)) + list(range(12, 16)))
    advice = sw.find_windows(make_forecast(wind=wind), now=NOW)
    scores = [w.score for w in advice.windows]
    assert scores == sorted(scores, reverse=True)


def test_window_label_is_human_readable():
    advice = sw.find_windows(make_forecast(), now=NOW)
    assert ":" in advice.best.label and "-" in advice.best.label


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
    wind[10] = float("nan")
    advice = sw.find_windows(make_forecast(wind=wind), now=NOW)
    assert sw.R_NO_WIND in advice.rejection_counts


def test_forecast_entirely_in_the_past_yields_nothing():
    old = make_forecast(start=NOW - pd.Timedelta(days=10))
    advice = sw.find_windows(old, now=NOW)
    assert not advice.has_window
    assert any("does not cover" in r for r in advice.reasons)
