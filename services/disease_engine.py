"""Late-blight risk for tomato and potato, from station observations.

Two measures, both explainable, both returned every time:

1. HUTTON CRITERIA (the operational standard). A "Hutton day" needs BOTH
   a daily minimum temperature >= 10 degC AND >= 6 hours at RH >= 90%.
   Two CONSECUTIVE Hutton days = HIGH risk.

2. HUMID HOURS (fallback). Simply how many of the last 24 hours sat at
   RH >= 90%, graded LOW / MODERATE / HIGH. Hutton needs a complete day;
   this always produces an answer, including mid-day.

Every result carries a `reasons` list in plain language, because a farmer being
told to spend money on fungicide deserves to know why.

DATA HONESTY: a day missing more than MAX_MISSING_FRACTION of its hours is
marked "insufficient data" rather than judged. We never infer a Hutton day from
a partial record - a missing night is exactly when the humid hours would have
happened, so guessing would bias us toward false confidence.

ML SWAP POINT: assess() takes a canonical DataFrame and returns a
RiskAssessment. Any model satisfying that signature - see the RiskModel
protocol - can replace the rule engine without touching callers.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date as Date
from typing import Protocol, runtime_checkable

import pandas as pd

import config
from services import data_processor as dp

# A day is judged only if at least this fraction of its 24 hours were observed.
# 25% missing (6 hours) is enough to hide an entire overnight humid spell.
MAX_MISSING_FRACTION = 0.25

HOURS_PER_DAY = 24

UNKNOWN = "UNKNOWN"


# --------------------------------------------------------------------------
# Result types
# --------------------------------------------------------------------------

@dataclass
class DayAssessment:
    """One calendar day evaluated against the Hutton criteria."""

    day: Date
    is_hutton_day: bool | None  # None when data was insufficient to judge
    sufficient_data: bool
    min_temp_c: float | None
    max_temp_c: float | None
    humid_hours: float
    hours_observed: int
    missing_fraction: float
    longest_humid_run_h: float
    humid_run_start: pd.Timestamp | None
    humid_run_end: pd.Timestamp | None
    reasons: list[str] = field(default_factory=list)

    @property
    def met_temp_criterion(self) -> bool:
        return self.min_temp_c is not None and self.min_temp_c >= config.HUTTON_MIN_TEMP_C

    @property
    def met_humidity_criterion(self) -> bool:
        return self.humid_hours >= config.HUTTON_MIN_HUMID_HOURS


@dataclass
class RiskAssessment:
    """The engine's answer for a point in time. This is the ML swap contract."""

    level: str                      # LOW / MODERATE / HIGH / UNKNOWN
    method: str                     # which measure drove the final level
    reasons: list[str]
    hutton_level: str
    humid_hours_level: str
    consecutive_hutton_days: int
    recent_hutton_days: list[Date]
    humid_hours_last_24h: float
    assessed_at: pd.Timestamp | None = None
    days: list[DayAssessment] = field(default_factory=list)
    data_warnings: list[str] = field(default_factory=list)

    @property
    def is_actionable(self) -> bool:
        """True when we have a real answer, not a data-shortage answer."""
        return self.level in config.RISK_LEVELS

    @property
    def color(self) -> str:
        return config.RISK_COLORS.get(self.level, config.RISK_COLORS[UNKNOWN])


@runtime_checkable
class RiskModel(Protocol):
    """Contract an ML model must satisfy to replace the rule engine."""

    def assess(self, df: pd.DataFrame) -> RiskAssessment: ...


# --------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------

def _severity(level: str) -> int:
    """Order risk levels so we can take the more cautious of two measures."""
    try:
        return config.RISK_LEVELS.index(level)
    except ValueError:
        return -1  # UNKNOWN sorts below LOW


def _describe_period(start: pd.Timestamp | None, end: pd.Timestamp | None) -> str:
    """Turn a humid spell into words a farmer would use: 'overnight', 'morning'."""
    if start is None or end is None:
        return ""
    h = start.hour
    if 20 <= h or h < 5:
        return "overnight"
    if 5 <= h < 12:
        return "in the morning"
    if 12 <= h < 17:
        return "during the afternoon"
    return "in the evening"


def _longest_run(mask: pd.Series) -> tuple[float, pd.Timestamp | None, pd.Timestamp | None]:
    """Longest consecutive run of True in an hourly mask, as (hours, start, end)."""
    if mask.empty or not mask.any():
        return 0.0, None, None

    best_len = cur_len = 0
    best_end_idx = cur_start_idx = None
    best_start_idx = None

    for idx, val in mask.items():
        if val:
            if cur_len == 0:
                cur_start_idx = idx
            cur_len += 1
            if cur_len > best_len:
                best_len = cur_len
                best_start_idx = cur_start_idx
                best_end_idx = idx
        else:
            cur_len = 0

    return float(best_len), best_start_idx, best_end_idx


def _hourly(df: pd.DataFrame) -> pd.DataFrame:
    """Ensure we are working on an hourly frame indexed by timestamp.

    The Hutton criteria are defined on hourly RH readings, so 15-minute station
    data is averaged to the hour first. Already-hourly input passes through.
    """
    if df.empty:
        return df
    if "timestamp" not in df.columns:
        raise ValueError("expected a canonical frame with a 'timestamp' column")

    ts = df["timestamp"]
    if len(ts) > 1:
        median_step = ts.diff().dropna().median()
        if median_step is not pd.NaT and median_step < pd.Timedelta(minutes=59):
            df = dp.to_hourly(df)

    return df.set_index("timestamp").sort_index()


# --------------------------------------------------------------------------
# Daily Hutton evaluation
# --------------------------------------------------------------------------

def evaluate_day(day_df: pd.DataFrame, day: Date) -> DayAssessment:
    """Evaluate one day's hourly readings against the Hutton criteria.

    `day_df` must be hourly, indexed by timestamp, covering a single date.
    """
    reasons: list[str] = []

    temps = day_df.get("temperature_c", pd.Series(dtype=float)).dropna()
    hums = day_df.get("humidity_pct", pd.Series(dtype=float)).dropna()

    # Sufficiency is judged on humidity: it is the variable that decides the
    # criterion, and it is the one most often lost to sensor dropouts.
    hours_observed = int(len(hums))
    missing_fraction = 1.0 - (hours_observed / HOURS_PER_DAY)
    sufficient = missing_fraction <= MAX_MISSING_FRACTION and len(temps) > 0

    humid_mask = hums >= config.HUTTON_RH_THRESHOLD_PCT
    humid_hours = float(humid_mask.sum())
    run_h, run_start, run_end = _longest_run(humid_mask)

    min_temp = float(temps.min()) if len(temps) else None
    max_temp = float(temps.max()) if len(temps) else None

    if not sufficient:
        missing_h = HOURS_PER_DAY - hours_observed
        reasons.append(
            f"Insufficient data for {day}: only {hours_observed} of 24 hours recorded "
            f"({missing_h} missing, {missing_fraction:.0%}). Not enough to judge blight risk."
        )
        return DayAssessment(
            day=day,
            is_hutton_day=None,
            sufficient_data=False,
            min_temp_c=min_temp,
            max_temp_c=max_temp,
            humid_hours=humid_hours,
            hours_observed=hours_observed,
            missing_fraction=missing_fraction,
            longest_humid_run_h=run_h,
            humid_run_start=run_start,
            humid_run_end=run_end,
            reasons=reasons,
        )

    met_temp = min_temp is not None and min_temp >= config.HUTTON_MIN_TEMP_C
    met_hum = humid_hours >= config.HUTTON_MIN_HUMID_HOURS
    is_hutton = met_temp and met_hum

    # Plain-language reasons, always stating the measured value.
    if met_temp:
        reasons.append(
            f"Lowest temperature was {min_temp:.1f}C, at or above the "
            f"{config.HUTTON_MIN_TEMP_C:.0f}C the blight fungus needs."
        )
    else:
        reasons.append(
            f"Lowest temperature was {min_temp:.1f}C, below the "
            f"{config.HUTTON_MIN_TEMP_C:.0f}C the blight fungus needs."
        )

    if met_hum:
        when = _describe_period(run_start, run_end)
        detail = f" (longest unbroken spell {run_h:.0f}h{', ' + when if when else ''})"
        reasons.append(
            f"Humidity stayed at or above {config.HUTTON_RH_THRESHOLD_PCT:.0f}% for "
            f"{humid_hours:.0f} hours{detail}."
        )
    else:
        reasons.append(
            f"Humidity reached {config.HUTTON_RH_THRESHOLD_PCT:.0f}% for only "
            f"{humid_hours:.0f} hours, short of the "
            f"{config.HUTTON_MIN_HUMID_HOURS} needed."
        )

    if missing_fraction > 0:
        reasons.append(
            f"Based on {hours_observed} of 24 hours of data."
        )

    return DayAssessment(
        day=day,
        is_hutton_day=is_hutton,
        sufficient_data=True,
        min_temp_c=min_temp,
        max_temp_c=max_temp,
        humid_hours=humid_hours,
        hours_observed=hours_observed,
        missing_fraction=missing_fraction,
        longest_humid_run_h=run_h,
        humid_run_start=run_start,
        humid_run_end=run_end,
        reasons=reasons,
    )


def evaluate_days(df: pd.DataFrame) -> list[DayAssessment]:
    """Evaluate every calendar day present in the frame, oldest first."""
    hourly = _hourly(df)
    if hourly.empty:
        return []

    out: list[DayAssessment] = []
    for day, group in hourly.groupby(hourly.index.date):
        out.append(evaluate_day(group, day))
    return sorted(out, key=lambda d: d.day)


def count_consecutive_hutton(days: list[DayAssessment]) -> tuple[int, list[Date]]:
    """Length of the Hutton run ending on the most recent judged day.

    THE TRAILING PARTIAL DAY IS SKIPPED, NOT TREATED AS A BREAK. When we assess
    at 06:00, today has only six hours of data and is correctly unjudgeable.
    Counting that as a break would make the run permanently zero and kill the
    HIGH pathway entirely, because today is always partial in live use.

    Exactly ONE trailing unjudged day is skipped. If the day before it is also
    unjudged we return zero rather than reaching back over an outage and
    presenting a stale run as current.

    An unjudged day in the MIDDLE still breaks the run: we will not bridge a gap
    and claim two consecutive Hutton days we did not observe.
    """
    idx = len(days) - 1

    # Skip "today", which is still in progress at assessment time.
    if idx >= 0 and days[idx].is_hutton_day is None:
        idx -= 1

    run: list[Date] = []
    while idx >= 0 and days[idx].is_hutton_day is True:
        run.append(days[idx].day)
        idx -= 1

    run.reverse()
    return len(run), run


# --------------------------------------------------------------------------
# Humid-hours fallback
# --------------------------------------------------------------------------

def humid_hours_risk(df: pd.DataFrame) -> tuple[str, float, list[str]]:
    """Grade the last N hours purely on time spent at RH >= 90%.

    Returns (level, humid_hours, reasons). Always produces an answer if there
    is any humidity data at all.
    """
    hourly = _hourly(df)
    reasons: list[str] = []

    if hourly.empty or "humidity_pct" not in hourly.columns:
        return UNKNOWN, 0.0, ["No humidity data available."]

    # Select by TIME, not by row count. hourly.tail(24) would silently reach
    # back days when the record has gaps, and then report those stale hours as
    # "the last 24 hours".
    end = hourly.index.max()
    start = end - pd.Timedelta(hours=config.HUMID_HOURS_WINDOW_HOURS - 1)
    window = hourly[hourly.index >= start]
    hums = window["humidity_pct"].dropna()

    if hums.empty:
        return UNKNOWN, 0.0, ["No humidity readings in the last 24 hours."]

    observed_fraction = len(hums) / config.HUMID_HOURS_WINDOW_HOURS
    coverage_ok = observed_fraction >= (1.0 - MAX_MISSING_FRACTION)

    humid_mask = hums >= config.HUTTON_RH_THRESHOLD_PCT
    humid_hours = float(humid_mask.sum())
    run_h, run_start, run_end = _longest_run(humid_mask)

    temps = window.get("temperature_c", pd.Series(dtype=float)).dropna()
    max_temp = float(temps.max()) if len(temps) else None

    # Temperature gate: humid but cold is not a blight risk.
    if max_temp is not None and max_temp < config.HUMID_HOURS_MIN_TEMP_C:
        reasons.append(
            f"Warmest it got was {max_temp:.1f}C, below the "
            f"{config.HUMID_HOURS_MIN_TEMP_C:.0f}C blight needs - risk stays low "
            f"despite {humid_hours:.0f} humid hours."
        )
        return "LOW", humid_hours, reasons

    # Coverage matters ASYMMETRICALLY here, because this measure is a count.
    #
    # Observing 6 humid hours PROVES at least 6 happened, however many hours we
    # missed - the unseen hours could only add more. So a positive finding is
    # self-validating and stands regardless of coverage.
    #
    # Observing ZERO humid hours in 8 of 24 proves nothing about the other 16,
    # which is exactly when an overnight humid spell hides. So the LOW verdict -
    # the only one that tells a farmer to relax - requires real coverage.
    if humid_hours >= config.HUMID_HOURS_HIGH:
        level = "HIGH"
    elif humid_hours >= config.HUMID_HOURS_MODERATE:
        level = "MODERATE"
    elif not coverage_ok:
        return UNKNOWN, humid_hours, [
            f"Only {len(hums)} of the last {config.HUMID_HOURS_WINDOW_HOURS} "
            f"hours had humidity readings ({1 - observed_fraction:.0%} missing), "
            f"and nothing humid was seen in them. Too little to call it safe."
        ]
    else:
        level = "LOW"

    when = _describe_period(run_start, run_end)
    if humid_hours > 0:
        detail = f" Longest unbroken spell was {run_h:.0f}h{' ' + when if when else ''}."
        reasons.append(
            f"Humidity stayed at or above {config.HUTTON_RH_THRESHOLD_PCT:.0f}% for "
            f"{humid_hours:.0f} of the last {len(hums)} hours.{detail}"
        )
    else:
        reasons.append(
            f"Humidity never reached {config.HUTTON_RH_THRESHOLD_PCT:.0f}% in the "
            f"last {len(hums)} hours."
        )

    if len(hums) < config.HUMID_HOURS_WINDOW_HOURS:
        reasons.append(
            f"Note: only {len(hums)} of the last "
            f"{config.HUMID_HOURS_WINDOW_HOURS} hours had readings."
        )

    return level, humid_hours, reasons


# --------------------------------------------------------------------------
# Top-level assessment
# --------------------------------------------------------------------------

def hutton_risk_level(n_consecutive: int, days: list[DayAssessment]) -> tuple[str, list[str]]:
    """Translate a Hutton run length into a risk level with reasons."""
    reasons: list[str] = []

    judged = [d for d in days if d.is_hutton_day is not None]
    if not judged:
        return UNKNOWN, ["Not enough complete days of data to apply the Hutton criteria."]

    if n_consecutive >= config.HUTTON_CONSECUTIVE_DAYS:
        span = f"{days[-1].day}" if n_consecutive == 1 else \
               f"{judged[-n_consecutive].day} to {judged[-1].day}"
        reasons.append(
            f"HIGH RISK: {n_consecutive} Hutton days in a row ({span}). "
            f"Two consecutive Hutton days is the official late-blight warning."
        )
        return "HIGH", reasons

    if n_consecutive == 1:
        reasons.append(
            f"One Hutton day so far ({judged[-1].day}). One more in a row triggers "
            f"a HIGH-risk blight warning."
        )
        return "MODERATE", reasons

    # No run ending today - but check whether one happened very recently.
    recent_hutton = [d.day for d in judged[-3:] if d.is_hutton_day]
    if recent_hutton:
        reasons.append(
            f"No Hutton day today, but conditions were met on "
            f"{', '.join(str(d) for d in recent_hutton)}."
        )
        return "MODERATE", reasons

    reasons.append("No Hutton days recently - temperature and humidity have not "
                   "both stayed in the danger zone.")
    return "LOW", reasons


def assess(df: pd.DataFrame, *, lookback_days: int = 7) -> RiskAssessment:
    """Assess late-blight risk from a canonical observation frame.

    This is the function an ML model would replace: same input, same output.
    """
    data_warnings: list[str] = []

    if df is None or df.empty:
        return RiskAssessment(
            level=UNKNOWN, method="none",
            reasons=["No weather data available to assess blight risk."],
            hutton_level=UNKNOWN, humid_hours_level=UNKNOWN,
            consecutive_hutton_days=0, recent_hutton_days=[],
            humid_hours_last_24h=0.0,
            data_warnings=["No data."],
        )

    missing = [c for c in ("temperature_c", "humidity_pct")
               if c not in df.columns or df[c].notna().sum() == 0]
    if missing:
        return RiskAssessment(
            level=UNKNOWN, method="none",
            reasons=[f"Cannot assess blight risk: missing {', '.join(missing)}."],
            hutton_level=UNKNOWN, humid_hours_level=UNKNOWN,
            consecutive_hutton_days=0, recent_hutton_days=[],
            humid_hours_last_24h=0.0,
            data_warnings=[f"missing columns: {missing}"],
        )

    hourly = _hourly(df)
    assessed_at = hourly.index.max() if len(hourly) else None

    # Restrict the Hutton view to the recent window.
    if assessed_at is not None and lookback_days:
        cutoff = assessed_at.normalize() - pd.Timedelta(days=lookback_days - 1)
        window_df = hourly[hourly.index >= cutoff].reset_index()
    else:
        window_df = hourly.reset_index()

    days = evaluate_days(window_df)
    n_consec, run_days = count_consecutive_hutton(days)

    hutton_level, hutton_reasons = hutton_risk_level(n_consec, days)
    hh_level, hh_hours, hh_reasons = humid_hours_risk(window_df)

    insufficient = [d for d in days if not d.sufficient_data]
    if insufficient:
        data_warnings.append(
            f"{len(insufficient)} of {len(days)} days had insufficient data: "
            + ", ".join(str(d.day) for d in insufficient[:5])
        )

    # How the two measures combine.
    #
    # Hutton is AUTHORITATIVE whenever it can be computed, and HIGH is reserved
    # for the official trigger - two consecutive Hutton days. That keeps the
    # headline claim honest: if Shamba Pulse says HIGH, the Hutton criteria
    # fired, full stop.
    #
    # Humid hours is a FALLBACK, not a competing score. It does two jobs:
    #   * it answers when Hutton cannot (a partial day, or too many gaps), and
    #   * it nudges LOW up to MODERATE when humidity has been sitting at 90%+
    #     without a completed Hutton day, so a building spell is not ignored.
    # It can never by itself produce HIGH - 6 humid hours in a rolling day is
    # simply not the same evidence as two consecutive qualifying days.
    if hutton_level == UNKNOWN:
        level, method = hh_level, "humid_hours"
    elif _severity(hh_level) > _severity(hutton_level) and hutton_level == "LOW":
        level, method = "MODERATE", "humid_hours_escalation"
    else:
        level, method = hutton_level, "hutton"

    reasons: list[str] = []
    if method == "humid_hours_escalation":
        reasons.append(
            "Watch closely: humidity has been high for long stretches even though "
            "no full Hutton day has been recorded yet."
        )
        reasons.extend(hh_reasons)
        reasons.extend(r for r in hutton_reasons if r not in reasons)
    else:
        primary = hutton_reasons if method == "hutton" else hh_reasons
        other = hh_reasons if method == "hutton" else hutton_reasons
        reasons.extend(primary)
        # Always include the other measure's reasoning so the farmer sees both.
        reasons.extend(r for r in other if r not in reasons)

    if insufficient:
        reasons.append(
            f"Note: {len(insufficient)} day(s) in the last {len(days)} could not be "
            f"judged because too many hourly readings were missing."
        )

    return RiskAssessment(
        level=level,
        method=method,
        reasons=reasons,
        hutton_level=hutton_level,
        humid_hours_level=hh_level,
        consecutive_hutton_days=n_consec,
        recent_hutton_days=run_days,
        humid_hours_last_24h=hh_hours,
        assessed_at=assessed_at,
        days=days,
        data_warnings=data_warnings,
    )
