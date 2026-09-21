"""Tests for the ERA5 cross-check that backs the dashboard panel.

The panel must never crash the dashboard and must never quote a number that
disagrees with data/rain_validation.csv, because the README quotes the same
figures.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import config  # noqa: E402
from analysis import validate_rain as vr  # noqa: E402

CSV = config.DATA_DIR / vr.VALIDATION_CSV_NAME


# --------------------------------------------------------------------------
# Loading: file present
# --------------------------------------------------------------------------

def test_loads_the_committed_csv_without_touching_the_network():
    """The committed cache must be enough - a deployed app should not need
    Open-Meteo just to draw this panel."""
    def explode(*a, **k):
        raise AssertionError("load_comparison must not fetch when cached")

    original = vr.requests.get
    vr.requests.get = explode
    try:
        data = vr.load_comparison()
    finally:
        vr.requests.get = original

    assert data.ok
    assert data.source == "cache"
    assert not data.df.empty


def test_cached_comparison_covers_the_canonical_window():
    data = vr.load_comparison()
    dates = pd.to_datetime(data.df["date"])
    assert str(dates.min().date()) == config.BACKTEST_WINDOW["start"]
    assert str(dates.max().date()) == config.BACKTEST_WINDOW["end"]


def test_required_columns_are_present():
    data = vr.load_comparison()
    for col in vr.REQUIRED_COLUMNS:
        assert col in data.df.columns


# --------------------------------------------------------------------------
# Loading: file missing or unusable
# --------------------------------------------------------------------------

def test_missing_file_without_fetch_reports_rather_than_raises():
    data = vr.load_comparison(Path("does_not_exist.csv"), allow_fetch=False)
    assert data.ok is False
    assert data.df.empty
    assert "not found" in data.error


def test_missing_file_with_unreachable_archive_degrades_gracefully(monkeypatch):
    """Cache gone AND Open-Meteo down: the panel must still not crash."""
    monkeypatch.setattr(vr, "fetch_era5", lambda *a, **k: pd.DataFrame())
    data = vr.load_comparison(Path("does_not_exist.csv"), allow_fetch=True)
    assert data.ok is False
    assert "unreachable" in data.error


def test_empty_file_is_rejected(tmp_path):
    p = tmp_path / "rain_validation.csv"
    p.write_text("date,station_mm,era5_mm\n", encoding="utf-8")
    data = vr.load_comparison(p, allow_fetch=False)
    assert data.ok is False
    assert "empty" in data.error


def test_file_with_wrong_columns_is_rejected(tmp_path):
    p = tmp_path / "rain_validation.csv"
    p.write_text("date,something_else\n2025-10-01,1\n", encoding="utf-8")
    data = vr.load_comparison(p, allow_fetch=False)
    assert data.ok is False
    assert "missing" in data.error


def test_unreadable_file_is_rejected(tmp_path):
    p = tmp_path / "rain_validation.csv"
    p.write_bytes(b"\x00\x01\x02 not a csv")
    data = vr.load_comparison(p, allow_fetch=False)
    assert data.ok is False


# --------------------------------------------------------------------------
# The numbers the panel and the README both quote
# --------------------------------------------------------------------------

def test_summary_matches_the_committed_csv_exactly():
    """Guards against the panel and the docs drifting apart."""
    df = pd.read_csv(CSV)
    stats = vr.summarise_comparison(df)

    assert stats["days"] == 92
    assert stats["station_total"] == pytest.approx(301.8, abs=0.05)
    assert stats["era5_total"] == pytest.approx(268.6, abs=0.05)
    assert stats["ratio"] == pytest.approx(1.12, abs=0.005)
    assert round(stats["agree_pct"]) == 62
    assert stats["spearman"] == pytest.approx(0.405, abs=0.005)
    # Exactly 0.375; the docs quote it rounded to 0.38.
    assert stats["jaccard"] == pytest.approx(0.375, abs=0.001)


def test_agreement_counts_partition_the_days():
    df = pd.read_csv(CSV)
    s = vr.summarise_comparison(df)
    total = (s["both_wet"] + s["only_station"]
             + s["only_era5"] + s["neither_wet"])
    assert total == s["days"]
    assert s["agree_days"] == s["both_wet"] + s["neither_wet"]


def test_seasonal_agreement_is_closer_than_daily_agreement():
    """The claim the panel makes in words, asserted as a number.

    Seasonal totals within ~12% while daily rank correlation is ~0.4 - that
    gap IS the point, so if it ever inverted the wording would be wrong.
    """
    s = vr.summarise_comparison(pd.read_csv(CSV))
    assert abs(s["ratio"] - 1) < 0.15, "seasonal totals should be close"
    assert s["spearman"] < 0.7, "daily correlation is expected to be weak"


def test_summary_survives_a_degenerate_frame():
    """Never raise on an all-zero or single-row frame."""
    flat = pd.DataFrame({"date": ["2025-10-01", "2025-10-02"],
                         "station_mm": [0.0, 0.0], "era5_mm": [0.0, 0.0]})
    s = vr.summarise_comparison(flat)
    assert s["days"] == 2
    assert s["neither_wet"] == 2
    assert s["pearson"] != s["pearson"]        # NaN, not an exception


# --------------------------------------------------------------------------
# Wording precision: ERA5 is reanalysis, not satellite
# --------------------------------------------------------------------------

def test_docs_do_not_call_era5_a_satellite_measurement():
    """ERA5 assimilates satellite data; it is not a satellite product.

    Calling it 'satellite rainfall' would be wrong, and a climate-literate
    judge would notice immediately.
    """
    import re
    for path in (Path("README.md"), Path("docs/DEVPOST.md"),
                 Path("app.py"), Path("analysis/validate_rain.py")):
        text = path.read_text(encoding="utf-8")
        for m in re.finditer(r"[^.\n]*ERA5[^.\n]*", text):
            sentence = m.group(0).lower()
            if "satellite" in sentence:
                assert ("reanalysis" in sentence
                        or "assimilat" in sentence
                        or "not a direct" in sentence
                        or "not direct" in sentence), (
                    f"{path}: ERA5 described with 'satellite' but not "
                    f"qualified as reanalysis: {m.group(0).strip()}")
