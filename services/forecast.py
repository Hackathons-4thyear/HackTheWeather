"""7-day hourly forecast for JKUAT from Open-Meteo (free, no API key).

The forecast is returned in the SAME canonical columns as station observations
(timestamp, temperature_c, humidity_pct, rain_mm, wind_speed_ms), so the disease
engine and the spray-window finder work on either source without special-casing.

Contract verified live on 2026-09-21: 168 hourly rows, wind already in m/s when
wind_speed_unit=ms is requested, times in Africa/Nairobi, elevation 1524 m
(correct for Juja). Units are requested explicitly rather than assumed.

Like the Conduit client, this never raises - a failed forecast must degrade the
dashboard, not crash it.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any

import pandas as pd
import requests

import config

# Open-Meteo variable name -> our canonical name.
_HOURLY_VARS: dict[str, str] = {
    "temperature_2m": "temperature_c",
    "relative_humidity_2m": "humidity_pct",
    "precipitation": "rain_mm",
    "wind_speed_10m": "wind_speed_ms",
    "wind_gusts_10m": "wind_gust_ms",
    "precipitation_probability": "rain_probability_pct",
}

# Cache the response briefly. A hackathon demo may refresh the dashboard often,
# and the forecast only updates hourly upstream anyway.
_CACHE_TTL_SECONDS = 900  # 15 minutes
_cache: dict[str, tuple[float, "ForecastResult"]] = {}


@dataclass
class ForecastResult:
    """Outcome of a forecast fetch. Never raises - inspect .ok and .error."""

    ok: bool
    df: pd.DataFrame = field(default_factory=pd.DataFrame)
    error: str | None = None
    fetched_at: pd.Timestamp | None = None
    elevation_m: float | None = None
    timezone: str | None = None
    from_cache: bool = False

    @property
    def n_hours(self) -> int:
        return len(self.df)


def _empty(error: str) -> ForecastResult:
    cols = ["timestamp", "temperature_c", "humidity_pct", "rain_mm", "wind_speed_ms"]
    return ForecastResult(ok=False, df=pd.DataFrame(columns=cols), error=error)


def fetch_forecast(
    *,
    lat: float | None = None,
    lon: float | None = None,
    days: int | None = None,
    use_cache: bool = True,
) -> ForecastResult:
    """Fetch the hourly forecast. Never raises."""
    lat = config.JKUAT_LAT if lat is None else lat
    lon = config.JKUAT_LON if lon is None else lon
    days = config.FORECAST_DAYS if days is None else days

    cache_key = f"{lat},{lon},{days}"
    if use_cache and cache_key in _cache:
        cached_at, cached = _cache[cache_key]
        if time.time() - cached_at < _CACHE_TTL_SECONDS:
            return ForecastResult(
                ok=cached.ok, df=cached.df.copy(), error=cached.error,
                fetched_at=cached.fetched_at, elevation_m=cached.elevation_m,
                timezone=cached.timezone, from_cache=True,
            )

    params: dict[str, Any] = {
        "latitude": lat,
        "longitude": lon,
        "hourly": ",".join(_HOURLY_VARS),
        # Request units explicitly so we never silently get km/h or degF.
        "wind_speed_unit": "ms",
        "temperature_unit": "celsius",
        "precipitation_unit": "mm",
        "timezone": "Africa/Nairobi",
        "forecast_days": days,
    }

    try:
        resp = requests.get(
            config.OPEN_METEO_URL,
            params=params,
            timeout=config.OPEN_METEO_TIMEOUT_SECONDS,
            headers={"User-Agent": config.USER_AGENT},
        )
    except requests.exceptions.Timeout:
        return _empty(f"forecast timed out after {config.OPEN_METEO_TIMEOUT_SECONDS}s")
    except requests.exceptions.RequestException as exc:
        return _empty(f"forecast network error: {exc}")

    if resp.status_code != 200:
        return _empty(f"forecast HTTP {resp.status_code}")

    try:
        payload = resp.json()
    except ValueError:
        return _empty("forecast response was not valid JSON")

    if "error" in payload:
        return _empty(f"Open-Meteo error: {payload.get('reason', 'unknown')}")

    hourly = payload.get("hourly")
    if not isinstance(hourly, dict) or "time" not in hourly:
        return _empty("forecast response had no hourly block")

    df = pd.DataFrame({"timestamp": pd.to_datetime(hourly["time"])})
    for api_name, canon in _HOURLY_VARS.items():
        if api_name in hourly:
            df[canon] = pd.to_numeric(pd.Series(hourly[api_name]), errors="coerce")

    # Sanity-check the units we asked for actually came back.
    units = payload.get("hourly_units", {})
    wind_unit = units.get("wind_speed_10m")
    if wind_unit and wind_unit not in ("m/s", "ms"):
        return _empty(f"forecast returned wind in {wind_unit!r}, expected m/s")

    result = ForecastResult(
        ok=True,
        df=df,
        fetched_at=pd.Timestamp.now(),
        elevation_m=payload.get("elevation"),
        timezone=payload.get("timezone"),
    )

    if use_cache:
        _cache[cache_key] = (time.time(), result)

    return result


def daily_outlook(df: pd.DataFrame) -> pd.DataFrame:
    """Collapse the hourly forecast into the per-day summary the UI shows."""
    if df.empty or "timestamp" not in df.columns:
        return pd.DataFrame()

    d = df.copy()
    d["day"] = d["timestamp"].dt.date

    agg: dict[str, Any] = {}
    if "temperature_c" in d.columns:
        agg["temp_min_c"] = ("temperature_c", "min")
        agg["temp_max_c"] = ("temperature_c", "max")
    if "humidity_pct" in d.columns:
        agg["humidity_max_pct"] = ("humidity_pct", "max")
        agg["humidity_mean_pct"] = ("humidity_pct", "mean")
    if "rain_mm" in d.columns:
        agg["rain_total_mm"] = ("rain_mm", "sum")
    if "wind_speed_ms" in d.columns:
        agg["wind_mean_ms"] = ("wind_speed_ms", "mean")
        agg["wind_max_ms"] = ("wind_speed_ms", "max")

    out = d.groupby("day").agg(**agg).reset_index()

    # Hours of rain and humid hours per day - what a farmer actually cares about.
    if "rain_mm" in d.columns:
        wet = d[d["rain_mm"] > config.SPRAY_RAIN_TRACE_MM]
        out["wet_hours"] = out["day"].map(
            wet.groupby("day").size()).fillna(0).astype(int)
    if "humidity_pct" in d.columns:
        humid = d[d["humidity_pct"] >= config.HUTTON_RH_THRESHOLD_PCT]
        out["humid_hours"] = out["day"].map(
            humid.groupby("day").size()).fillna(0).astype(int)

    return out
