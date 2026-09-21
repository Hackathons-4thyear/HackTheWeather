"""Find the best upcoming windows to spray fungicide.

An hour is SPRAYABLE when all of these hold:
  * it is not raining that hour (<= the trace threshold),
  * it will not rain for the next SPRAY_DRY_HOURS_REQUIRED hours, so the
    fungicide has time to become rain-fast instead of washing straight off, and
  * wind sits inside SPRAY_WIND_MIN_MS..SPRAY_WIND_MAX_MS - calm air lets
    droplets hang and drift unpredictably, strong wind blows them off target.

Consecutive sprayable hours merge into a window. Windows shorter than
SPRAY_WINDOW_MIN_HOURS are dropped - not worth mixing a tank for.

Windows are then ranked, preferring early morning and late afternoon (less
evaporation loss, fewer active pollinators) and wind near the middle of the
usable band.

Every window carries plain-language reasons, and every REJECTED hour records
why it was rejected, so the dashboard can explain "no windows found" honestly
instead of just showing an empty list.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import pandas as pd

import config


@dataclass
class SprayWindow:
    """One usable stretch of time for spraying."""

    start: pd.Timestamp
    end: pd.Timestamp          # inclusive end hour
    duration_hours: int
    mean_wind_ms: float
    max_wind_ms: float
    min_wind_ms: float
    dry_hours_after: float     # how long it stays dry from the window's end
    score: float
    preferred_overlap_h: int
    reasons: list[str] = field(default_factory=list)

    @property
    def label(self) -> str:
        """Short human label, e.g. 'Tue 06:00-10:00'."""
        same_day = self.start.date() == self.end.date()
        if same_day:
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


def _rain_free_ahead(rain: pd.Series, idx: int, hours: int) -> tuple[bool, float]:
    """Is it dry for `hours` after position `idx`? Returns (ok, dry_hours_seen).

    A truncated forecast counts as NOT ok: we will not promise a dry spell we
    cannot actually see. The caller reports that honestly.
    """
    window = rain.iloc[idx + 1: idx + 1 + hours]
    if len(window) < hours:
        return False, float(len(window))

    wet = window > config.SPRAY_RAIN_TRACE_MM
    if wet.any():
        return False, float(wet.to_numpy().argmax())
    return True, float(hours)


def _dry_run_after(rain: pd.Series, idx: int) -> float:
    """How many consecutive dry hours follow position `idx`."""
    n = 0
    for i in range(idx + 1, len(rain)):
        if rain.iloc[i] > config.SPRAY_RAIN_TRACE_MM:
            break
        n += 1
    return float(n)


def find_windows(
    forecast_df: pd.DataFrame,
    *,
    now: pd.Timestamp | None = None,
    lookahead_hours: int | None = None,
    currently_raining: bool | None = None,
) -> SprayAdvice:
    """Find sprayable windows in an hourly forecast frame.

    `forecast_df` needs canonical columns: timestamp, rain_mm, wind_speed_ms.
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

    df = forecast_df.sort_values("timestamp").reset_index(drop=True)
    start_at = now.floor("h")
    horizon = start_at + pd.Timedelta(hours=lookahead_hours)
    df = df[(df["timestamp"] >= start_at) & (df["timestamp"] < horizon)]
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

    # A live "it is raining right now" reading beats the forecast for hour 0.
    if currently_raining:
        advice.reasons.append(
            "It is raining at the station right now, so the next hour is ruled out."
        )

    rejections: dict[str, int] = {}
    sprayable: list[bool] = []
    dry_after: list[float] = []

    for i in range(len(df)):
        reject: str | None = None

        if currently_raining and i == 0:
            reject = "raining now"
        elif rain.iloc[i] > config.SPRAY_RAIN_TRACE_MM:
            reject = "rain during the hour"
        elif pd.isna(wind.iloc[i]):
            reject = "no wind reading"
        elif wind.iloc[i] < config.SPRAY_WIND_MIN_MS:
            reject = "too calm"
        elif wind.iloc[i] > config.SPRAY_WIND_MAX_MS:
            reject = "too windy"
        else:
            ok_dry, seen = _rain_free_ahead(rain, i, config.SPRAY_DRY_HOURS_REQUIRED)
            if not ok_dry:
                reject = ("forecast ends too soon to confirm"
                          if i + 1 + config.SPRAY_DRY_HOURS_REQUIRED > len(df)
                          else "rain expected within "
                               f"{config.SPRAY_DRY_HOURS_REQUIRED}h")

        sprayable.append(reject is None)
        dry_after.append(_dry_run_after(rain, i))
        if reject:
            rejections[reject] = rejections.get(reject, 0) + 1

    advice.rejection_counts = rejections

    # Merge consecutive sprayable hours into windows.
    windows: list[SprayWindow] = []
    i = 0
    while i < len(sprayable):
        if not sprayable[i]:
            i += 1
            continue
        j = i
        while j + 1 < len(sprayable) and sprayable[j + 1]:
            j += 1

        length = j - i + 1
        if length >= config.SPRAY_WINDOW_MIN_HOURS:
            windows.append(_build_window(df, wind, i, j, dry_after[j]))
        i = j + 1

    windows.sort(key=lambda w: (-w.score, w.start))
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
    df: pd.DataFrame, wind: pd.Series, i: int, j: int, dry_after: float
) -> SprayWindow:
    """Assemble a SprayWindow with its score and plain-language reasons."""
    start = df["timestamp"].iloc[i]
    end = df["timestamp"].iloc[j]
    length = j - i + 1
    w = wind.iloc[i: j + 1]

    hours = [df["timestamp"].iloc[k].hour for k in range(i, j + 1)]
    preferred_overlap = sum(1 for h in hours if h in config.SPRAY_PREFERRED_HOURS)

    # Score in 0..1 from three components, weighted by how much each matters.
    band_mid = (config.SPRAY_WIND_MIN_MS + config.SPRAY_WIND_MAX_MS) / 2
    band_half = (config.SPRAY_WIND_MAX_MS - config.SPRAY_WIND_MIN_MS) / 2
    wind_centrality = 1.0 - min(abs(float(w.mean()) - band_mid) / band_half, 1.0)
    time_of_day = preferred_overlap / length
    duration_score = min(length / 4.0, 1.0)  # 4h+ is as good as it needs to be

    score = 0.45 * time_of_day + 0.35 * wind_centrality + 0.20 * duration_score

    reasons = [
        f"No rain expected for at least {config.SPRAY_DRY_HOURS_REQUIRED} hours "
        f"after spraying, so the fungicide has time to stick.",
        f"Wind {w.mean():.1f} m/s (range {w.min():.1f}-{w.max():.1f}), inside the "
        f"{config.SPRAY_WIND_MIN_MS:.0f}-{config.SPRAY_WIND_MAX_MS:.0f} m/s "
        f"band - strong enough to carry the spray, gentle enough not to blow it away.",
        f"Window is {length} hour(s) long.",
    ]
    if preferred_overlap == length:
        reasons.append("Falls entirely in the cool part of the day - less spray "
                       "lost to evaporation.")
    elif preferred_overlap:
        reasons.append(f"{preferred_overlap} of {length} hours fall in the cooler "
                       f"part of the day.")
    else:
        reasons.append("Falls in the heat of the day - more spray will evaporate, "
                       "so early morning would be better if you can wait.")

    return SprayWindow(
        start=start,
        end=end,
        duration_hours=length,
        mean_wind_ms=float(w.mean()),
        max_wind_ms=float(w.max()),
        min_wind_ms=float(w.min()),
        dry_hours_after=dry_after,
        score=round(score, 3),
        preferred_overlap_h=preferred_overlap,
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
