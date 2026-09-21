"""Tests for the raw-to-canonical mapping, against the REAL Conduit schema.

The field names and shapes here were confirmed against the live station on
2026-09-21, so these tests pin the actual contract rather than a guess.
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import config  # noqa: E402
from services import data_processor as dp  # noqa: E402

# One real record, copied verbatim from the live API response. Note that every
# value arrives as a STRING, and the timestamp is UTC with a Z suffix.
REAL_ROW = {
    "ts": "2026-09-20T00:00:26Z", "rg1": "0", "rg2": "0",
    "rg1tt": "0", "rg2tt": "0", "rg1tp": "4.8", "rg2tp": "0",
    "temp_bmx": "16.5", "press_bmx": "852.4", "temp_mcp": "16.7",
    "temp_sht": "16.9", "humidity_sht": "82.5",
    "si1145_vis": "260", "si1145_ir": "253", "si1145_uv": "0",
    "wind_spd": "0.1", "wind_dir": "121", "wind_gust": "0.4",
    "wind_gust_dir": "0.4", "heat_idx": "16.9",
    "wet_bulb_temp": "14.8", "wet_bulb_globe_temp": "12.6",
}


def real_rows(n: int = 8, *, rg1tt: list[float] | None = None) -> list[dict]:
    """A run of real-shaped records at the station's ~15-minute cadence."""
    rows = []
    for i in range(n):
        row = dict(REAL_ROW)
        ts = pd.Timestamp("2026-09-20T00:00:26Z") + pd.Timedelta(minutes=15 * i)
        row["ts"] = ts.strftime("%Y-%m-%dT%H:%M:%SZ")
        if rg1tt is not None:
            row["rg1tt"] = str(rg1tt[i])
        rows.append(row)
    return rows


# --------------------------------------------------------------------------
# The real schema maps correctly
# --------------------------------------------------------------------------

def test_real_conduit_columns_all_map():
    mapping = dp.build_column_map(REAL_ROW.keys())
    assert mapping["timestamp"] == "ts"
    assert mapping["temperature_c"] == "temp_sht"
    assert mapping["humidity_pct"] == "humidity_sht"
    assert mapping["wind_speed_ms"] == "wind_spd"
    assert mapping["wind_gust_ms"] == "wind_gust"
    assert mapping["wind_dir_deg"] == "wind_dir"
    assert mapping["pressure_hpa"] == "press_bmx"
    assert mapping["wet_bulb_c"] == "wet_bulb_temp"
    assert mapping["uv_raw"] == "si1145_uv"
    assert mapping["ir_raw"] == "si1145_ir"
    assert mapping["vis_raw"] == "si1145_vis"


def test_no_required_column_is_missing_from_the_real_schema():
    canon = dp.to_canonical(real_rows())
    assert dp.check_required(canon) == []


def test_string_values_are_coerced_to_numbers():
    """The API sends everything as strings, including '0' and '16.9'."""
    canon = dp.to_canonical(real_rows())
    for col in ("temperature_c", "humidity_pct", "wind_speed_ms", "rain_mm"):
        assert pd.api.types.is_numeric_dtype(canon[col]), col


def test_wind_speed_maps_from_wind_spd_not_wind_gust():
    """'wind_spd' is the abbreviation the station actually uses."""
    canon = dp.to_canonical(real_rows())
    assert canon["wind_speed_ms"].iloc[0] == pytest.approx(0.1)
    assert canon["wind_gust_ms"].iloc[0] == pytest.approx(0.4)


# --------------------------------------------------------------------------
# UTC to local time - a 3-hour error would corrupt every Hutton day
# --------------------------------------------------------------------------

def test_utc_timestamps_are_converted_to_nairobi_local():
    """The station reports UTC with a Z; Kenya is UTC+3.

    Getting this wrong shifts every 'overnight humid hours' window by three
    hours and moves the daily boundary, which would silently corrupt the
    Hutton evaluation.
    """
    canon = dp.to_canonical([REAL_ROW])
    assert canon["timestamp"].iloc[0] == pd.Timestamp("2026-09-20 03:00:26")


def test_parsed_timestamps_are_timezone_naive():
    """Downstream resampling assumes naive local time."""
    canon = dp.to_canonical(real_rows())
    assert canon["timestamp"].dt.tz is None


def test_timestamps_are_sorted_and_deduplicated():
    rows = real_rows(4) + real_rows(4)      # exact duplicates
    canon = dp.to_canonical(rows)
    assert len(canon) == 4
    assert canon["timestamp"].is_monotonic_increasing


# --------------------------------------------------------------------------
# Rainfall from the running daily total
# --------------------------------------------------------------------------

def test_rainfall_is_the_difference_between_consecutive_totals():
    totals = pd.Series([0.0, 0.0, 0.2, 1.4, 1.6, 3.8])
    incr = dp.rainfall_from_cumulative(totals)
    assert list(incr) == pytest.approx([0.0, 0.0, 0.2, 1.2, 0.2, 2.2])
    assert incr.sum() == pytest.approx(3.8)


def test_counter_reset_is_not_read_as_negative_rain():
    """The station zeroes the total at UTC midnight."""
    totals = pd.Series([4.6, 4.8, 0.0, 0.2, 0.4])
    incr = dp.rainfall_from_cumulative(totals)
    assert (incr >= 0).all(), "rain can never be negative"
    assert list(incr) == pytest.approx([0.0, 0.2, 0.0, 0.2, 0.2])


def test_rain_accumulated_before_a_reset_is_counted():
    """A reset to a NON-zero value means rain fell during the reset interval."""
    totals = pd.Series([4.8, 0.6])
    incr = dp.rainfall_from_cumulative(totals)
    assert incr.iloc[1] == pytest.approx(0.6)


def test_first_reading_assumes_no_rain_rather_than_inventing_it():
    incr = dp.rainfall_from_cumulative(pd.Series([4.8, 5.0]))
    assert incr.iloc[0] == 0.0


def test_to_canonical_prefers_the_cumulative_total_over_rg1():
    """rg1 under-reports badly on the real station; rg1tt is the truth.

    Here rg1 stays at '0' (as it really does) while rg1tt climbs to 1.4 mm.
    Trusting rg1 would tell a farmer it never rained.
    """
    rows = real_rows(6, rg1tt=[0.0, 0.0, 0.2, 1.4, 1.4, 1.4])
    for r in rows:
        r["rg1"] = "0"
    canon = dp.to_canonical(rows)
    assert canon["rain_mm"].sum() == pytest.approx(1.4)
    assert (canon["rain_mm"] > 0).sum() == 2


def test_falls_back_to_per_interval_rain_when_no_cumulative_field():
    """An export without rg1tt must still produce rainfall."""
    rows = []
    for i, mm in enumerate([0.0, 0.4, 0.0, 1.1]):
        rows.append({
            "ts": f"2026-09-20T0{i}:00:00Z",
            "rg1": str(mm), "temp_sht": "18", "humidity_sht": "80",
            "wind_spd": "2.0",
        })
    canon = dp.to_canonical(rows)
    assert canon["rain_mm"].sum() == pytest.approx(1.5)


# --------------------------------------------------------------------------
# Rain gauge 2 is broken and must never be trusted
# --------------------------------------------------------------------------

def test_rain_gauge_2_is_not_trusted():
    """Measured: rg2tt reconstructed 297 mm over 11 DRY days, 207 resets.

    A broken sensor is worse than a missing one, because it looks like data.
    """
    assert dp.RAIN_GAUGE_2_TRUSTED is False


def test_gauge_2_never_fills_gaps_in_gauge_1():
    rows = real_rows(4, rg1tt=[0.0, 0.0, 0.0, 0.0])
    for r in rows:
        r["rg2"] = "99.0"          # nonsense from the faulty gauge
    canon = dp.to_canonical(rows)
    assert canon["rain_mm"].sum() == 0.0, "gauge 2 must not leak into rain_mm"
    assert "rain_mm_2" not in canon.columns


# --------------------------------------------------------------------------
# Cleaning
# --------------------------------------------------------------------------

def test_implausible_values_become_nan():
    rows = real_rows(3)
    rows[1]["temp_sht"] = "999"
    rows[2]["humidity_sht"] = "150"
    canon = dp.to_canonical(rows)
    assert pd.isna(canon["temperature_c"].iloc[1])
    assert pd.isna(canon["humidity_pct"].iloc[2])


def test_empty_input_does_not_raise():
    assert dp.to_canonical([]).empty
    assert dp.to_canonical(pd.DataFrame()).empty


def test_missing_timestamp_column_raises_clearly():
    with pytest.raises(ValueError, match="timestamp"):
        dp.to_canonical([{"temp_sht": "18", "humidity_sht": "80"}])


# --------------------------------------------------------------------------
# Hourly resampling
# --------------------------------------------------------------------------

def test_hourly_sums_rain_and_averages_the_rest():
    rows = real_rows(8, rg1tt=[0, 0, 0.2, 0.4, 0.6, 0.6, 0.6, 0.6])
    canon = dp.to_canonical(rows)
    hourly = dp.to_hourly(canon)
    assert hourly["rain_mm"].sum() == pytest.approx(canon["rain_mm"].sum())


def test_hourly_keeps_gaps_visible_instead_of_zero_filling_rain():
    """An hour with no readings must be NaN, not a confident 0 mm."""
    rows = real_rows(2) + real_rows(2)
    for i, r in enumerate(rows[2:]):
        ts = pd.Timestamp("2026-09-20T06:00:26Z") + pd.Timedelta(minutes=15 * i)
        r["ts"] = ts.strftime("%Y-%m-%dT%H:%M:%SZ")
    canon = dp.to_canonical(rows)
    hourly = dp.to_hourly(canon)
    empty_hours = hourly[hourly["temperature_c"].isna()]
    assert len(empty_hours) > 0
    assert empty_hours["rain_mm"].isna().all(), "a data gap is not a dry hour"


# --------------------------------------------------------------------------
# Staleness - a successful fetch is not the same as fresh data
# --------------------------------------------------------------------------

def test_stale_data_is_flagged_even_when_the_fetch_succeeded():
    """The API answering is not the same as the data being current.

    The station publishes on a lag, so a healthy connection routinely returns
    readings many hours old. Calling that "live" would be misleading.
    """
    from services import data_source as ds

    fresh = pd.DataFrame({
        "timestamp": [pd.Timestamp.now() - pd.Timedelta(minutes=20)],
        "temperature_c": [20.0], "humidity_pct": [80.0],
    })
    old = pd.DataFrame({
        "timestamp": [pd.Timestamp.now() - pd.Timedelta(hours=13)],
        "temperature_c": [20.0], "humidity_pct": [80.0],
    })

    assert ds.DataStatus(source=ds.LIVE, df=fresh).is_stale is False
    assert ds.DataStatus(source=ds.LIVE, df=old).is_stale is True


def test_unknown_age_counts_as_stale():
    """Better to warn wrongly than present unknown-age data as current."""
    from services import data_source as ds
    assert ds.DataStatus(source=ds.LIVE, df=pd.DataFrame()).is_stale is True


def test_staleness_boundary_follows_config():
    from services import data_source as ds
    just_under = pd.DataFrame({
        "timestamp": [pd.Timestamp.now()
                      - pd.Timedelta(hours=config.STALE_AFTER_HOURS)
                      + pd.Timedelta(minutes=5)],
        "temperature_c": [20.0], "humidity_pct": [80.0],
    })
    just_over = pd.DataFrame({
        "timestamp": [pd.Timestamp.now()
                      - pd.Timedelta(hours=config.STALE_AFTER_HOURS)
                      - pd.Timedelta(minutes=5)],
        "temperature_c": [20.0], "humidity_pct": [80.0],
    })
    assert ds.DataStatus(source=ds.LIVE, df=just_under).is_stale is False
    assert ds.DataStatus(source=ds.LIVE, df=just_over).is_stale is True


# --------------------------------------------------------------------------
# The API's todate bound is EXCLUSIVE
# --------------------------------------------------------------------------

def test_fetch_recent_asks_through_tomorrow():
    """todate is exclusive on this endpoint, verified against the live API.

    Requesting fromdate=D&todate=D+1 returns only day D. So including today
    means asking through tomorrow - otherwise the newest day is silently
    dropped from every live fetch.
    """
    from datetime import date, timedelta
    from services import conduit_api

    seen = {}

    def fake_range(fromdate, todate, **kw):
        seen["from"], seen["to"] = fromdate, todate
        return conduit_api.ConduitResult(ok=True, rows=[])

    original = conduit_api.fetch_range
    conduit_api.fetch_range = fake_range
    try:
        conduit_api.fetch_recent(days=7)
    finally:
        conduit_api.fetch_range = original

    assert seen["to"] == date.today() + timedelta(days=1)
    assert seen["from"] == date.today() - timedelta(days=6)


def test_empty_data_array_is_no_rows_not_a_parse_error():
    """The station returns {"status":"success","data":[]} for an unlogged day.

    Reporting that as an unrecognised shape would send us hunting for a parser
    bug that does not exist.
    """
    from services.conduit_api import _extract_rows
    rows, err = _extract_rows(
        {"status": "success", "headers": ["ts", "rg1"], "data": []})
    assert rows == []
    assert err is None
