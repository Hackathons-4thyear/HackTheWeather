"""Find the best upcoming windows to spray fungicide.

An hour is SPRAYABLE when all of these hold:
  * it is not raining that hour (<= the trace threshold),
  * it will not rain for the next SPRAY_DRY_HOURS_REQUIRED hours, so the
    fungicide has time to become rain-fast instead of washing straight off,
  * wind sits inside SPRAY_WIND_MIN_MS..SPRAY_WIND_MAX_MS - calm air lets
    droplets hang and drift unpredictably, strong wind blows them off target,
  * humidity is below SPRAY_MAX_HUMIDITY_PCT, because dew or fog on the leaf
    dilutes the spray and makes it run off, and
  * the hour falls in daylight - nobody sprays at 3am.

Consecutive sprayable hours merge into a run, the run is CLIPPED to daylight
(rather than thrown away, so an overnight stretch can still yield its morning
tail), and anything left shorter than SPRAY_MIN_WINDOW_HOURS is dropped.

Windows are ranked preferring the cooler parts of the day and wind near the
middle of the usable band.

IMPORTANT ASYMMETRY: only the SPRAYING must happen in daylight. The 6-hour
rain-free requirement after spraying is checked against the full forecast,
night hours included - rain at 2am still washes off a 7pm spray.

Every window carries plain-language reasons, and every rejected hour records
why, so the dashboard can explain "no windows found" honestly instead of just
showing an empty list.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import pandas as pd

import config

# Rejection reason labels, used as dict keys in the tally the UI shows.
R_RAINING_NOW = "raining now"
R_RAIN_HOUR = "rain during the hour"
R_RAIN_SOON = f"rain expected within {config.SPRAY_DRY_HOURS_REQUIRED}h"
R_TRUNCATED = "forecast ends too soon to confirm"
R_NO_WIND = "no wind reading"
R_CALM = "too calm"
R_WINDY = "too windy"
R_WET_LEAF = "leaves likely wet"
R_NIGHT = "outside daylight"
R_SHORT = "window too short"


@dataclass
class SprayWindow:
    """One usable stretch of time for spraying.

    `start` is inclusive and `end` is EXCLUSIVE, so a window covering 09:00
    through 16:59 is start=09:00, end=17:00, duration_hours=8.0. Clipping to
    daylight can put either bound on a half hour (e.g. 06:30).
    """

    start: pd.Timestamp
    end: pd.Timestamp
    duration_hours: float
    mean_wind_ms: float
    max_wind_ms: float
    min_wind_ms: float
    max_humidity_pct: float | None
    dry_hours_after: float
    score: float
    preferred_overlap_h: int
    was_clipped: bool = False
    reasons: list[str] = field(default_factory=list)

    @property
    def label(self) -> str:
        """Short human label, e.g. 'Tue 23 Sep 06:30-10:00'."""
        if self.start.date() == self.end.date():
            return f"{self.start:%a %d %b} {self.start:%H:%M}-{self.end:%H:%M}"
        return f"{self.start:%a %d %b %H:%M} - {self.end:%a %d %b %H:%M}"

    @property
    def quality(self) -> str:
        """GOOD / FAIR, for colour-coding in the UI."""
        return "GOOD" if self.score >= 0.6 else "FAIR"


@dataclass
class SprayAdvice:
    """The full answer: windows found, plus why hours were rejected."""

    windows: list[SprayWindow] = field(default_factory=list)
    reasons: list[str] = field(default_factory=list)
    hours_considered: int = 0
    rejection_counts: dict[str, int] = field(default_factory=dict)
    evaluated_from: pd.Timestamp | None = None
    evaluated_to: pd.Timestamp | None = None

    @property
    def has_window(self) -> bool:
        return bool(self.windows)

    @property
    def best(self) -> SprayWindow | None:
        return self.windows[0] if self.windows else None


# --------------------------------------------------------------------------
# Daylight helpers
# --------------------------------------------------------------------------

def _daylight_bounds(day: pd.Timestamp) -> tuple[pd.Timestamp, pd.Timestamp]:
    """The daylight interval for the calendar day containing `day`."""
    base = day.normalize()
    start = base + pd.Timedelta(
        hours=config.SPRAY_DAYLIGHT_START.hour,
        minutes=config.SPRAY_DAYLIGHT_START.minute,
    )
    end = base + pd.Timedelta(
        hours=config.SPRAY_DAYLIGHT_END.hour,
        minutes=config.SPRAY_DAYLIGHT_END.minute,
    )
    return start, end


def _hour_touches_daylight(ts: pd.Timestamp) -> bool:
    """Does the hour beginning at `ts` overlap daylight at all?

    Hours 06:00 and 18:00 partially overlap a 06:30-18:30 span, so they are
    allowed through here and trimmed precisely at the interval stage.
    """
    hour_start = ts
    hour_end = ts + pd.Timedelta(hours=1)
    day_start, day_end = _daylight_bounds(ts)
    return hour_start < day_end and hour_end > day_start


def _clip_to_daylight(
    start: pd.Timestamp, end: pd.Timestamp
) -> list[tuple[pd.Timestamp, pd.Timestamp]]:
    """Intersect [start, end) with daylight, returning 0..n sub-intervals.

    A run spanning several days yields one sub-interval per day, so
    'Mon 19:00 -> Tue 08:00' collapses to just 'Tue 06:30 -> Tue 08:00'.
    """
    out: list[tuple[pd.Timestamp, pd.Timestamp]] = []
    day = start.normalize()
    last = end.normalize()
    while day <= last:
        d_start, d_end = _daylight_bounds(day)
        s = max(start, d_start)
        e = min(end, d_end)
        if s < e:
            out.append((s, e))
        day += pd.Timedelta(days=1)
    return out


# --------------------------------------------------------------------------
# Rain lookahead
# --------------------------------------------------------------------------

def _rain_free_ahead(rain: pd.Series, idx: int, hours: int) -> tuple[bool, bool]:
    """Is it dry for `hours` after position `idx`?

    Returns (ok, truncated). `truncated` distinguishes "we saw rain coming" from
    "the forecast ran out", because those need different explanations.

    This deliberately looks through NIGHT hours: rain at 2am still washes off a
    7pm spray, so the dry requirement ignores the daylight limit.
    """
    window = rain.iloc[idx + 1: idx + 1 + hours]
    if len(window) < hours:
        return False, True
    if (window > config.SPRAY_RAIN_TRACE_MM).any():
        return False, False
    return True, False


def _dry_run_after(rain: pd.Series, idx: int) -> float:
    """How many consecutive dry hours follow position `idx`."""
    n = 0
    for i in range(idx + 1, len(rain)):
        if rain.iloc[i] > config.SPRAY_RAIN_TRACE_MM:
            break
        n += 1
    return float(n)


# --------------------------------------------------------------------------
# Main entry point
# --------------------------------------------------------------------------

def find_windows(
    forecast_df: pd.DataFrame,
    *,
    now: pd.Timestamp | None = None,
    lookahead_hours: int | None = None,
    currently_raining: bool | None = None,
) -> SprayAdvice:
    """Find sprayable windows in an hourly forecast frame.

    `forecast_df` needs canonical columns: timestamp, rain_mm, wind_speed_ms,
    and ideally humidity_pct for the wet-leaf check.
    `currently_raining` lets the caller override with a live station reading,
    which is more trustworthy than the forecast for the current hour.
    """
    advice = SprayAdvice()

    if forecast_df is None or forecast_df.empty:
        advice.reasons.append("No forecast data available, so no spray windows.")
        return advice

    missing = [c for c in ("timestamp", "rain_mm", "wind_speed_ms")
               if c not in forecast_df.columns]
    if missing:
        advice.reasons.append(
            f"Cannot find spray windows: forecast is missing {', '.join(missing)}."
        )
        return advice

    lookahead_hours = lookahead_hours or config.SPRAY_LOOKAHEAD_HOURS
    now = pd.Timestamp.now() if now is None else now

    full = forecast_df.sort_values("timestamp").reset_index(drop=True)
    start_at = now.floor("h")
    horizon = start_at + pd.Timedelta(hours=lookahead_hours)
    df = full[(full["timestamp"] >= start_at) & (full["timestamp"] < horizon)]
    df = df.reset_index(drop=True)

    if df.empty:
        advice.reasons.append(
            f"The forecast does not cover the next {lookahead_hours} hours."
        )
        return advice

    advice.hours_considered = len(df)
    advice.evaluated_from = df["timestamp"].iloc[0]
    advice.evaluated_to = df["timestamp"].iloc[-1]

    rain = df["rain_mm"].fillna(0.0)
    wind = df["wind_speed_ms"]
    has_humidity = "humidity_pct" in df.columns
    humidity = df["humidity_pct"] if has_humidity else None

    if not has_humidity:
        advice.reasons.append(
            "Note: forecast has no humidity, so wet leaves could not be checked."
        )

    if currently_raining:
        advice.reasons.append(
            "It is raining at the station right now, so the next hour is ruled out."
        )

    rejections: dict[str, int] = {}
    sprayable: list[bool] = []

    for i in range(len(df)):
        ts = df["timestamp"].iloc[i]
        reject: str | None = None

        if currently_raining and i == 0:
            reject = R_RAINING_NOW
        elif not _hour_touches_daylight(ts):
            reject = R_NIGHT
        elif rain.iloc[i] > config.SPRAY_RAIN_TRACE_MM:
            reject = R_RAIN_HOUR
        elif has_humidity and pd.notna(humidity.iloc[i]) and \
                humidity.iloc[i] >= config.SPRAY_MAX_HUMIDITY_PCT:
            reject = R_WET_LEAF
        elif pd.isna(wind.iloc[i]):
            reject = R_NO_WIND
        elif wind.iloc[i] < config.SPRAY_WIND_MIN_MS:
            reject = R_CALM
        elif wind.iloc[i] > config.SPRAY_WIND_MAX_MS:
            reject = R_WINDY
        else:
            # Dry-spell check runs against the FULL frame, not the trimmed one,
            # so a window near the horizon can still be confirmed if the wider
            # forecast covers it.
            full_idx = full.index[full["timestamp"] == ts]
            if len(full_idx):
                ok_dry, truncated = _rain_free_ahead(
                    full["rain_mm"].fillna(0.0), int(full_idx[0]),
                    config.SPRAY_DRY_HOURS_REQUIRED,
                )
            else:
                ok_dry, truncated = _rain_free_ahead(
                    rain, i, config.SPRAY_DRY_HOURS_REQUIRED)
            if not ok_dry:
                reject = R_TRUNCATED if truncated else R_RAIN_SOON

        sprayable.append(reject is None)
        if reject:
            rejections[reject] = rejections.get(reject, 0) + 1

    # Merge consecutive sprayable hours, clip each run to daylight, keep what
    # is still long enough.
    windows: list[SprayWindow] = []
    i = 0
    while i < len(sprayable):
        if not sprayable[i]:
            i += 1
            continue
        j = i
        while j + 1 < len(sprayable) and sprayable[j + 1]:
            j += 1

        run_start = df["timestamp"].iloc[i]
        run_end = df["timestamp"].iloc[j] + pd.Timedelta(hours=1)

        for seg_start, seg_end in _clip_to_daylight(run_start, run_end):
            hours = (seg_end - seg_start) / pd.Timedelta(hours=1)
            if hours < config.SPRAY_MIN_WINDOW_HOURS:
                rejections[R_SHORT] = rejections.get(R_SHORT, 0) + 1
                continue
            clipped = (seg_start != run_start) or (seg_end != run_end)
            windows.append(
                _build_window(df, full, seg_start, seg_end, clipped)
            )
        i = j + 1

    windows.sort(key=lambda w: (-w.score, w.start))
    advice.rejection_counts = rejections
    advice.windows = windows

    if windows:
        advice.reasons.insert(
            0,
            f"Found {len(windows)} spray window(s) in the next {lookahead_hours} hours."
        )
    else:
        advice.reasons.append(
            f"No spray window in the next {lookahead_hours} hours."
        )
        if rejections:
            top = sorted(rejections.items(), key=lambda kv: -kv[1])
            detail = ", ".join(f"{reason} ({n}h)" for reason, n in top[:3])
            advice.reasons.append(f"Main blockers: {detail}.")

    return advice


def _build_window(
    df: pd.DataFrame,
    full: pd.DataFrame,
    start: pd.Timestamp,
    end: pd.Timestamp,
    was_clipped: bool,
) -> SprayWindow:
    """Assemble a SprayWindow from the hours overlapping [start, end)."""
    ts = df["timestamp"]
    overlap = df[(ts + pd.Timedelta(hours=1) > start) & (ts < end)]

    w = overlap["wind_speed_ms"].dropna()
    hum = overlap["humidity_pct"].dropna() if "humidity_pct" in overlap.columns \
        else pd.Series(dtype=float)

    duration = (end - start) / pd.Timedelta(hours=1)

    # Hours whose clock hour falls in the cooler preferred band.
    preferred = sum(1 for t in overlap["timestamp"]
                    if t.hour in config.SPRAY_PREFERRED_HOURS)

    # Dry hours following the last hour of this window.
    last_ts = overlap["timestamp"].iloc[-1]
    full_idx = full.index[full["timestamp"] == last_ts]
    rain_series = full["rain_mm"].fillna(0.0)
    dry_after = _dry_run_after(rain_series, int(full_idx[0])) if len(full_idx) else 0.0

    band_mid = (config.SPRAY_WIND_MIN_MS + config.SPRAY_WIND_MAX_MS) / 2
    band_half = (config.SPRAY_WIND_MAX_MS - config.SPRAY_WIND_MIN_MS) / 2
    mean_wind = float(w.mean()) if len(w) else 0.0
    wind_centrality = 1.0 - min(abs(mean_wind - band_mid) / band_half, 1.0)
    time_of_day = preferred / len(overlap) if len(overlap) else 0.0
    duration_score = min(duration / 4.0, 1.0)  # 4h+ is as good as it needs to be

    score = 0.45 * time_of_day + 0.35 * wind_centrality + 0.20 * duration_score

    reasons = [
        f"No rain expected for at least {config.SPRAY_DRY_HOURS_REQUIRED} hours "
        f"after spraying, so the fungicide has time to stick.",
        f"Wind {mean_wind:.1f} m/s (range {w.min():.1f}-{w.max():.1f}), inside the "
        f"{config.SPRAY_WIND_MIN_MS:.0f}-{config.SPRAY_WIND_MAX_MS:.0f} m/s band - "
        f"strong enough to carry the spray, gentle enough not to blow it away.",
    ]
    if len(hum):
        reasons.append(
            f"Humidity {hum.min():.0f}-{hum.max():.0f}%, below the "
            f"{config.SPRAY_MAX_HUMIDITY_PCT:.0f}% at which dew would dilute the "
            f"spray and run off the leaf."
        )
    reasons.append(f"Window is {duration:.1f} hour(s) long.")

    if was_clipped:
        reasons.append(
            f"Trimmed to daylight ({config.SPRAY_DAYLIGHT_START:%H:%M}-"
            f"{config.SPRAY_DAYLIGHT_END:%H:%M}) - the dry spell runs longer, but "
            f"spraying in the dark is not practical."
        )

    if preferred == len(overlap):
        reasons.append("Falls entirely in the cool part of the day - less spray "
                       "lost to evaporation.")
    elif preferred:
        reasons.append(f"{preferred} of {len(overlap)} hours fall in the cooler "
                       f"part of the day.")
    else:
        reasons.append("Falls in the heat of the day - more spray will evaporate, "
                       "so early morning would be better if you can wait.")

    return SprayWindow(
        start=start,
        end=end,
        duration_hours=round(duration, 2),
        mean_wind_ms=mean_wind,
        max_wind_ms=float(w.max()) if len(w) else 0.0,
        min_wind_ms=float(w.min()) if len(w) else 0.0,
        max_humidity_pct=float(hum.max()) if len(hum) else None,
        dry_hours_after=dry_after,
        score=round(score, 3),
        preferred_overlap_h=preferred,
        was_clipped=was_clipped,
        reasons=reasons,
    )


def is_raining_now(observations: pd.DataFrame) -> bool | None:
    """Is it raining at the station right now? None when we cannot tell.

    Looks at the most recent reading only. Returns None rather than False when
    there is no data - "we do not know" and "it is dry" are different answers.
    """
    if observations is None or observations.empty:
        return None
    if "rain_mm" not in observations.columns:
        return None

    recent = observations.sort_values("timestamp").tail(1)
    val = recent["rain_mm"].iloc[0]
    if pd.isna(val):
        return None
    return bool(val > config.SPRAY_RAIN_TRACE_MM)
