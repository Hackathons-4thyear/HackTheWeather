"""Turn raw Conduit station output into a clean, canonical DataFrame.

The station's raw column names are terse and undocumented (ts, rg1, rg2,
temp_bmx, ...). Everything downstream of this module speaks only the canonical
names in CANONICAL_COLUMNS, so if the station renames a field we fix it here and
nowhere else.

Matching is alias-based and case/punctuation-insensitive, with an ordered
preference list per canonical name: for temperature we prefer the SHT sensor
(same chip as the humidity sensor, so temp and RH agree), then BMX, then MCP.
"""

from __future__ import annotations

import re
from typing import Any, Iterable, Mapping, Sequence

import numpy as np
import pandas as pd

import config

# --------------------------------------------------------------------------
# Canonical schema
# --------------------------------------------------------------------------

CANONICAL_COLUMNS: tuple[str, ...] = (
    "timestamp",
    "temperature_c",
    "humidity_pct",
    "rain_mm",
    "wind_speed_ms",
    "wind_gust_ms",
    "wind_dir_deg",
    "pressure_hpa",
    "wet_bulb_c",
    "uv_raw",
    "ir_raw",
    "vis_raw",
)

# Columns the decision engine cannot work without.
REQUIRED_COLUMNS: tuple[str, ...] = (
    "timestamp",
    "temperature_c",
    "humidity_pct",
    "rain_mm",
    "wind_speed_ms",
)

# Ordered alias candidates per canonical name. FIRST MATCH WINS, so the most
# trustworthy sensor for each variable is listed first.
COLUMN_ALIASES: dict[str, tuple[str, ...]] = {
    "timestamp": (
        "timestamp", "ts", "datetime", "date_time", "time", "date",
        "recorded_at", "created_at", "reading_time", "logged_at", "dt",
    ),
    # SHT first: it is the same chip as the humidity sensor, so pairing temp+RH
    # from one sensor avoids cross-sensor bias in the Hutton calculation.
    "temperature_c": (
        "temp_sht", "sht_temp", "temperature_sht", "tempsht",
        "temp_bmx", "bmx_temp", "temperature_bmx", "tempbmx",
        "temp_mcp", "mcp_temp", "temperature_mcp", "tempmcp",
        "temperature_c", "temperature", "temp", "air_temp", "airtemp", "temp_c",
    ),
    "humidity_pct": (
        "humidity_sht", "sht_humidity", "hum_sht", "humiditysht",
        "humidity_pct", "humidity", "rh", "relative_humidity", "hum",
        "humidity_percent", "rel_hum",
    ),
    # Rain gauge 1 is the primary; rg2 is handled separately as a cross-check.
    "rain_mm": (
        "rain_mm", "rainfall", "rain", "precipitation", "precip",
        "rg1", "rain_gauge_1", "raingauge1", "rg_1", "rain1", "rg1_mm",
    ),
    "rain_mm_2": (
        "rg2", "rain_gauge_2", "raingauge2", "rg_2", "rain2", "rg2_mm",
    ),
    "wind_speed_ms": (
        "wind_speed_ms", "wind_speed", "windspeed", "wind", "ws", "wind_spd",
    ),
    "wind_gust_ms": (
        "wind_gust_ms", "wind_gust", "windgust", "gust", "wind_gust_speed",
    ),
    "wind_dir_deg": (
        "wind_dir_deg", "wind_direction", "wind_dir", "winddir", "wd",
        "wind_heading", "direction",
    ),
    "pressure_hpa": (
        "pressure_bmx", "bmx_pressure", "pressure_hpa", "pressure",
        "baro", "barometric_pressure", "press", "bmp_pressure",
    ),
    "wet_bulb_c": (
        "wet_bulb_c", "wet_bulb", "wetbulb", "wb", "wet_bulb_temp",
    ),
    # SI1145 light channels. RAW COUNTS - deliberately NOT called W/m^2.
    "uv_raw": ("uv", "uv_raw", "si1145_uv", "uv_index_raw", "uvi", "si1145uv"),
    "ir_raw": ("ir", "ir_raw", "si1145_ir", "infrared", "si1145ir"),
    "vis_raw": ("vis", "vis_raw", "visible", "si1145_vis", "light", "si1145vis"),
}

# Physically plausible bounds. Readings outside these are sensor faults, not
# weather, and are set to NaN rather than silently skewing a daily minimum.
PLAUSIBLE_RANGES: dict[str, tuple[float, float]] = {
    "temperature_c": (-10.0, 60.0),     # Juja sits ~1500 m; never near these.
    "humidity_pct": (0.0, 100.0),       # RH is a percentage by definition.
    "rain_mm": (0.0, 200.0),            # Per 15 min; 200 mm would be a record.
    "wind_speed_ms": (0.0, 60.0),
    "wind_gust_ms": (0.0, 80.0),
    "wind_dir_deg": (0.0, 360.0),
    "pressure_hpa": (800.0, 1100.0),    # ~1500 m elevation sits near 850 hPa.
    "wet_bulb_c": (-10.0, 50.0),
}


def _normalise(name: Any) -> str:
    """Lowercase and strip punctuation so 'Temp (BMX)' matches 'temp_bmx'."""
    return re.sub(r"[^a-z0-9]", "", str(name).strip().lower())


def build_column_map(columns: Iterable[Any]) -> dict[str, str]:
    """Map canonical name -> raw column name, for whatever columns exist.

    Exact normalised match first across all canonical names, then a substring
    pass for the leftovers. A raw column is never claimed twice.
    """
    raw = list(columns)
    norm_to_raw: dict[str, str] = {}
    for col in raw:
        norm_to_raw.setdefault(_normalise(col), col)

    mapping: dict[str, str] = {}
    claimed: set[str] = set()

    # Pass 1 - exact normalised alias match.
    for canon, aliases in COLUMN_ALIASES.items():
        for alias in aliases:
            hit = norm_to_raw.get(_normalise(alias))
            if hit is not None and hit not in claimed:
                mapping[canon] = hit
                claimed.add(hit)
                break

    # Pass 2 - substring match for anything still unresolved.
    for canon, aliases in COLUMN_ALIASES.items():
        if canon in mapping:
            continue
        for alias in aliases:
            a = _normalise(alias)
            if len(a) < 3:  # 'ws', 'rh' etc. are too short to match safely
                continue
            for norm, rawcol in norm_to_raw.items():
                if rawcol in claimed:
                    continue
                if a in norm or norm in a:
                    mapping[canon] = rawcol
                    claimed.add(rawcol)
                    break
            if canon in mapping:
                break

    return mapping


# --- Helpers the exploration script uses to classify unknown columns ---------

def _matches_any(col: Any, needles: Sequence[str]) -> bool:
    n = _normalise(col)
    return any(x in n for x in needles)


def looks_like_rain(col: Any) -> bool:
    return _matches_any(col, ("rain", "precip", "rg1", "rg2", "raingauge"))


def looks_like_humidity(col: Any) -> bool:
    n = _normalise(col)
    if "humid" in n:
        return True
    # Bare 'rh' or a prefixed/suffixed variant, but not 'through' etc.
    return bool(re.fullmatch(r"(sht)?rh(sht|pct|percent)?", n))


def looks_like_temperature(col: Any) -> bool:
    n = _normalise(col)
    if "wetbulb" in n or "wbgt" in n:
        return False  # related but not air temperature
    return "temp" in n or bool(re.fullmatch(r"t[0-9]?", n))


def guess_timestamp_column(df: pd.DataFrame) -> str | None:
    """Best-guess timestamp column: alias match first, then parseability."""
    mapping = build_column_map(df.columns)
    if "timestamp" in mapping:
        return mapping["timestamp"]

    # Fall back to whichever column parses as a date most often.
    best: tuple[float, str] | None = None
    for col in df.columns:
        sample = df[col].dropna().head(200)
        if sample.empty:
            continue
        parsed = parse_timestamps(sample)
        rate = parsed.notna().mean()
        if rate > 0.8 and (best is None or rate > best[0]):
            best = (rate, col)
    return best[1] if best else None


def parse_timestamps(series: pd.Series) -> pd.Series:
    """Parse a column of timestamps in whatever format the station used.

    Handles ISO strings, common local formats, and unix epochs in s / ms.
    Returns tz-naive local (Africa/Nairobi) times - the station reports local
    time and tz-naive keeps pandas resampling simple.
    """
    s = series.copy()

    # Numeric -> epoch. Distinguish seconds from milliseconds by magnitude.
    numeric = pd.to_numeric(s, errors="coerce")
    if numeric.notna().mean() > 0.9:
        median = float(numeric.dropna().median()) if numeric.notna().any() else 0.0
        if median > 1e11:  # milliseconds since 1970
            return pd.to_datetime(numeric, unit="ms", errors="coerce")
        if median > 1e8:  # seconds since 1970
            return pd.to_datetime(numeric, unit="s", errors="coerce")

    parsed = pd.to_datetime(s, errors="coerce", format="ISO8601")
    if parsed.notna().mean() < 0.5:
        # Station exports are often day-first (dd/mm/yyyy).
        parsed = pd.to_datetime(s, errors="coerce", dayfirst=True)

    # Drop any tz so everything downstream is consistently naive local time.
    if getattr(parsed.dtype, "tz", None) is not None:
        parsed = parsed.dt.tz_convert(config.LOCAL_TZ).dt.tz_localize(None)
    return parsed


# --------------------------------------------------------------------------
# Main entry point
# --------------------------------------------------------------------------

def to_canonical(
    data: pd.DataFrame | Sequence[Mapping[str, Any]],
    *,
    clip_implausible: bool = True,
) -> pd.DataFrame:
    """Convert raw station rows into the canonical frame.

    Accepts a DataFrame or a list of dicts (as returned by the Conduit API).
    Output is sorted by timestamp, de-duplicated, numeric-typed, and contains
    only canonical columns. Columns the station does not provide are simply
    absent - we never invent a variable (there is no soil-moisture sensor).
    """
    df = pd.DataFrame(data) if not isinstance(data, pd.DataFrame) else data.copy()
    if df.empty:
        return pd.DataFrame(columns=list(REQUIRED_COLUMNS))

    mapping = build_column_map(df.columns)

    out = pd.DataFrame(index=df.index)
    for canon, raw in mapping.items():
        out[canon] = df[raw]

    if "timestamp" not in out.columns:
        raise ValueError(
            f"No timestamp column found. Raw columns were: {list(df.columns)}"
        )

    out["timestamp"] = parse_timestamps(out["timestamp"])
    out = out[out["timestamp"].notna()]

    # Everything except the timestamp is numeric.
    for col in out.columns:
        if col != "timestamp":
            out[col] = pd.to_numeric(out[col], errors="coerce")

    if clip_implausible:
        for col, (lo, hi) in PLAUSIBLE_RANGES.items():
            if col in out.columns:
                bad = (out[col] < lo) | (out[col] > hi)
                out.loc[bad, col] = np.nan

    # If the second rain gauge exists, use it to fill gaps in the primary.
    if "rain_mm_2" in out.columns:
        if "rain_mm" in out.columns:
            out["rain_mm"] = out["rain_mm"].fillna(out["rain_mm_2"])
        else:
            out["rain_mm"] = out["rain_mm_2"]
        out = out.drop(columns=["rain_mm_2"])

    out = (
        out.sort_values("timestamp")
        .drop_duplicates(subset="timestamp", keep="last")
        .reset_index(drop=True)
    )

    ordered = [c for c in CANONICAL_COLUMNS if c in out.columns]
    return out[ordered]


def check_required(df: pd.DataFrame) -> list[str]:
    """Return the engine-critical canonical columns that are missing or empty."""
    missing = []
    for col in REQUIRED_COLUMNS:
        if col not in df.columns or df[col].notna().sum() == 0:
            missing.append(col)
    return missing


def to_hourly(df: pd.DataFrame) -> pd.DataFrame:
    """Resample 15-minute readings to hourly, aggregating each variable sanely.

    Rain SUMS over the hour (it is an accumulation); everything else is a mean,
    except gust which takes the max. Hours with no readings at all are kept as
    NaN rows so gaps stay visible instead of silently closing up.
    """
    if df.empty or "timestamp" not in df.columns:
        return df

    how: dict[str, str] = {
        "temperature_c": "mean",
        "humidity_pct": "mean",
        "rain_mm": "sum",
        "wind_speed_ms": "mean",
        "wind_gust_ms": "max",
        "wind_dir_deg": "mean",
        "pressure_hpa": "mean",
        "wet_bulb_c": "mean",
        "uv_raw": "mean",
        "ir_raw": "mean",
        "vis_raw": "mean",
    }
    agg = {c: how[c] for c in df.columns if c in how}
    if not agg:
        return df

    indexed = df.set_index("timestamp")
    hourly = indexed.resample("1h").agg(agg)

    # resample().sum() turns an all-NaN hour into 0.0; restore NaN so a data gap
    # is not mistaken for a genuinely dry hour.
    if "rain_mm" in hourly.columns:
        counts = indexed["rain_mm"].resample("1h").count()
        hourly.loc[counts == 0, "rain_mm"] = np.nan

    return hourly.reset_index()
