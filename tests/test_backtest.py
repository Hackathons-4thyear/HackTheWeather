"""Tests for the historical backtest.

The property that matters most is NO LOOKAHEAD: at each simulated alert time the
engine must see only observations up to that moment. If future data leaks in,
the backtest proves nothing.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import config  # noqa: E402
from analysis import backtest as bt  # noqa: E402
from services import disease_engine as de  # noqa: E402


def make_history(days: int = 12, humid_from: int | None = None) -> pd.DataFrame:
    """Hourly canonical history. From `humid_from` onward, nights are humid."""
    rows = []
    start = pd.Timestamp("2025-10-01 00:00")
    for d in range(days):
        humid = humid_from is not None and d >= humid_from
        for h in range(24):
            rows.append({
                "timestamp": start + pd.Timedelta(days=d, hours=h),
                "temperature_c": 14.0 if h < 6 else 22.0,
                "humidity_pct": (95.0 if h < 10 else 65.0) if humid else 60.0,
                "rain_mm": 0.0,
                "wind_speed_ms": 2.5,
            })
    return pd.DataFrame(rows)


# --------------------------------------------------------------------------
# No lookahead - the property the whole backtest rests on
# --------------------------------------------------------------------------

def test_engine_never_sees_data_after_the_alert_time(monkeypatch):
    """Capture every frame handed to the engine and assert none reaches ahead."""
    seen: list[tuple[pd.Timestamp, pd.Timestamp]] = []
    real_assess = de.assess

    def spy(df, **kwargs):
        if df is not None and not df.empty and "timestamp" in df.columns:
            seen.append((df["timestamp"].max(), None))
        return real_assess(df, **kwargs)

    monkeypatch.setattr(bt.de, "assess", spy)

    results = bt.run_backtest(make_history(12), alert_hour=6, with_spray=False)
    assert not results.empty
    assert len(seen) == len(results)

    for (max_ts, _), (_, row) in zip(seen, results.iterrows()):
        assert max_ts <= row["as_of"], (
            f"engine saw {max_ts}, which is after the alert time {row['as_of']}"
        )


def test_each_day_sees_strictly_more_data_than_the_last():
    results = bt.run_backtest(make_history(12), alert_hour=6, with_spray=False)
    counts = results["rows_seen"].tolist()
    assert counts == sorted(counts)
    assert counts[0] < counts[-1]


def test_result_for_a_day_is_unaffected_by_later_data():
    """Truncating the history after day 8 must not change days 1-8."""
    full = make_history(12, humid_from=3)
    truncated = full[full["timestamp"] < pd.Timestamp("2025-10-09 00:00")]

    a = bt.run_backtest(full, alert_hour=6, with_spray=False)
    b = bt.run_backtest(truncated, alert_hour=6, with_spray=False)

    common = b["date"].tolist()
    a_sub = a[a["date"].isin(common)].reset_index(drop=True)
    b_sub = b.reset_index(drop=True)

    assert a_sub["level"].tolist() == b_sub["level"].tolist()
    assert a_sub["consecutive_hutton_days"].tolist() == \
        b_sub["consecutive_hutton_days"].tolist()


# --------------------------------------------------------------------------
# Alert detection
# --------------------------------------------------------------------------

def test_sustained_humid_nights_eventually_trigger_high():
    results = bt.run_backtest(make_history(14, humid_from=2), alert_hour=6,
                              with_spray=False)
    assert (results["level"] == "HIGH").any(), "a long wet spell must reach HIGH"
    assert (results["consecutive_hutton_days"] >= config.HUTTON_CONSECUTIVE_DAYS).any()


def test_dry_history_never_triggers_high():
    results = bt.run_backtest(make_history(14), alert_hour=6, with_spray=False)
    assert not (results["level"] == "HIGH").any()
    assert not results["would_send"].any()


def test_high_only_ever_comes_from_the_hutton_criteria():
    """The headline promise: HIGH means Hutton fired."""
    results = bt.run_backtest(make_history(14, humid_from=2), alert_hour=6,
                              with_spray=False)
    for _, r in results[results["level"] == "HIGH"].iterrows():
        assert r["hutton_level"] == "HIGH"
        assert r["consecutive_hutton_days"] >= config.HUTTON_CONSECUTIVE_DAYS


def test_every_sent_alert_has_both_languages():
    results = bt.run_backtest(make_history(14, humid_from=2), alert_hour=6,
                              with_spray=False)
    sent = results[results["would_send"]]
    assert len(sent)
    for _, r in sent.iterrows():
        assert r["sms_en"].strip()
        assert r["sms_sw"].strip()
        assert len(r["sms_en"]) <= config.SMS_MAX_CHARS
        assert len(r["sms_sw"]) <= config.SMS_MAX_CHARS


# --------------------------------------------------------------------------
# De-duplication
# --------------------------------------------------------------------------

def test_changes_only_keeps_transitions_and_high_days():
    results = bt.run_backtest(make_history(14, humid_from=2), alert_hour=6,
                              with_spray=False)
    trimmed = bt.changes_only(results)
    assert len(trimmed) <= len(results)
    # Every HIGH day survives, because that is the one a farmer must not miss.
    assert (results["level"] == "HIGH").sum() == (trimmed["level"] == "HIGH").sum()


def test_changes_only_handles_an_empty_frame():
    assert bt.changes_only(pd.DataFrame()).empty


# --------------------------------------------------------------------------
# Warmup and degenerate input
# --------------------------------------------------------------------------

def test_warmup_days_are_skipped():
    """The first days have no history behind them for a Hutton run."""
    hist = make_history(10)
    results = bt.run_backtest(hist, alert_hour=6, with_spray=False)
    first_data_day = hist["timestamp"].min().normalize()
    assert results["as_of"].min() >= first_data_day + pd.Timedelta(days=bt.WARMUP_DAYS)


def test_empty_history_returns_empty_results():
    assert bt.run_backtest(pd.DataFrame()).empty


def test_date_range_filters_are_respected():
    results = bt.run_backtest(make_history(20), alert_hour=6, with_spray=False,
                              start=pd.Timestamp("2025-10-10"),
                              end=pd.Timestamp("2025-10-15"))
    assert results["date"].min() >= pd.Timestamp("2025-10-10").date()
    assert results["date"].max() <= pd.Timestamp("2025-10-15").date()


def test_alert_hour_changes_what_the_engine_sees():
    early = bt.run_backtest(make_history(10), alert_hour=6, with_spray=False)
    late = bt.run_backtest(make_history(10), alert_hour=18, with_spray=False)
    assert late["rows_seen"].iloc[0] > early["rows_seen"].iloc[0]
