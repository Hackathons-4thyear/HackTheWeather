"""Decide where station observations come from, and say so honestly.

Three sources, tried in order:

  LIVE    - the Conduit station API. What we want, and what the judging asks
            for: real data from the JKUAT station.
  CACHED  - the downloaded history in data/. Still REAL station data, just not
            fresh. Used when the API is down or credentials are missing.
  DEMO    - synthetic. Used only when there is no real data at all, and
            labelled "DEMO DATA" everywhere it surfaces, loudly.

The rule this module exists to enforce: the UI must never be unable to tell the
user which of the three it is looking at. Every loader returns a DataStatus
carrying the source, so the banner cannot drift out of sync with reality.

The Open-Meteo forecast is a separate matter - it needs no key and is real live
data even when the station is unreachable, so it is reported independently.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, timedelta
from pathlib import Path

import numpy as np
import pandas as pd

import config
from services import conduit_api
from services import data_processor as dp

LIVE = "live"
CACHED = "cached"
DEMO = "demo"


@dataclass
class DataStatus:
    """Where the observations came from, and how much to trust them."""

    source: str                      # LIVE / CACHED / DEMO
    df: pd.DataFrame = field(default_factory=pd.DataFrame)
    ok: bool = True
    headline: str = ""
    detail: str = ""
    attempts: list[str] = field(default_factory=list)
    path: str | None = None

    @property
    def is_demo(self) -> bool:
        return self.source == DEMO

    @property
    def is_real(self) -> bool:
        """True for live AND cached - both are genuine station readings."""
        return self.source in (LIVE, CACHED)

    @property
    def latest(self) -> pd.Timestamp | None:
        if self.df.empty or "timestamp" not in self.df.columns:
            return None
        return self.df["timestamp"].max()

    @property
    def age(self) -> pd.Timedelta | None:
        latest = self.latest
        return None if latest is None else pd.Timestamp.now() - latest

    def age_text(self) -> str:
        age = self.age
        if age is None:
            return "unknown"
        hours = age.total_seconds() / 3600
        if hours < 1:
            return f"{int(age.total_seconds() / 60)} min old"
        if hours < 48:
            return f"{hours:.0f} h old"
        return f"{age.days} days old"


# --------------------------------------------------------------------------
# Individual loaders
# --------------------------------------------------------------------------

def load_live(days: int = 7) -> DataStatus:
    """Pull recent readings straight from the Conduit station."""
    if not config.CONDUIT_API_KEY or not config.CONDUIT_EMAIL:
        return DataStatus(source=LIVE, ok=False,
                          detail="CONDUIT_API_KEY / CONDUIT_EMAIL not set")

    result = conduit_api.fetch_recent(days=days)
    if not result.ok:
        return DataStatus(source=LIVE, ok=False,
                          detail=f"Conduit API: {result.error}")
    if not result.rows:
        return DataStatus(source=LIVE, ok=False,
                          detail="Conduit API returned no rows")

    try:
        canon = dp.to_canonical(result.rows)
    except Exception as exc:  # noqa: BLE001 - never crash the dashboard
        return DataStatus(source=LIVE, ok=False,
                          detail=f"could not parse station data: {exc}")

    missing = dp.check_required(canon)
    if "humidity_pct" in missing or "temperature_c" in missing:
        return DataStatus(source=LIVE, ok=False,
                          detail=f"station data missing {missing}")

    return DataStatus(
        source=LIVE, df=canon, ok=True,
        headline="Live data from the JKUAT Conduit station",
        detail=f"{len(canon):,} readings, latest {canon['timestamp'].max():%Y-%m-%d %H:%M}",
    )


def load_cached() -> DataStatus:
    """Fall back to the downloaded history. Still real station data."""
    candidates = [
        config.DATA_DIR / "conduit_history.parquet",
        config.DATA_DIR / "conduit_history.csv",
    ]
    candidates += sorted(config.DATA_DIR.glob("*.csv"))
    candidates += sorted(config.DATA_DIR.glob("*.xlsx"))

    for path in candidates:
        if not path.exists():
            continue
        try:
            if path.suffix == ".parquet":
                raw = pd.read_parquet(path)
            elif path.suffix == ".csv":
                raw = pd.read_csv(path, low_memory=False)
            else:
                raw = pd.read_excel(path)
        except Exception:  # noqa: BLE001 - try the next candidate
            continue

        try:
            if "timestamp" in raw.columns and "humidity_pct" in raw.columns:
                raw["timestamp"] = pd.to_datetime(raw["timestamp"], errors="coerce")
                canon = raw.dropna(subset=["timestamp"])
            else:
                canon = dp.to_canonical(raw)
        except Exception:  # noqa: BLE001
            continue

        if canon.empty:
            continue

        missing = dp.check_required(canon)
        if "humidity_pct" in missing or "temperature_c" in missing:
            continue

        return DataStatus(
            source=CACHED, df=canon, ok=True, path=str(path),
            headline="Cached station history (real Conduit data, not live)",
            detail=f"{len(canon):,} readings from {path.name}, "
                   f"latest {canon['timestamp'].max():%Y-%m-%d %H:%M}",
        )

    return DataStatus(source=CACHED, ok=False,
                      detail=f"no usable history file in {config.DATA_DIR}")


def make_demo(days: int = 10, *, seed: int = 20261) -> pd.DataFrame:
    """Synthetic observations, for when there is no real data at all.

    Shaped like a Juja short-rains week - humid nights, warm days, scattered
    rain - so the dashboard demonstrates its behaviour. This is NOT station
    data and every surface that shows it must say so.
    """
    rng = np.random.default_rng(seed)
    end = pd.Timestamp.now().floor("15min")
    ts = pd.date_range(end - pd.Timedelta(days=days), end, freq="15min")
    n = len(ts)
    hour = ts.hour + ts.minute / 60

    temp = 20.5 + 5.0 * np.sin((hour - 9) / 24 * 2 * np.pi) + rng.normal(0, 0.7, n)
    hum = np.clip(93 - 2.3 * (temp - 15) + rng.normal(0, 3.5, n), 20, 100)
    wet = rng.random(n) < 0.06
    rain = np.where(wet, rng.gamma(1.5, 0.8, n), 0.0)
    wind = np.clip(rng.gamma(2.2, 1.0, n), 0, None)

    return pd.DataFrame({
        "timestamp": ts,
        "temperature_c": np.round(temp, 2),
        "humidity_pct": np.round(hum, 1),
        "rain_mm": np.round(rain, 2),
        "wind_speed_ms": np.round(wind, 2),
        "wind_gust_ms": np.round(wind * rng.uniform(1.1, 1.8, n), 2),
        "pressure_hpa": np.round(rng.normal(858, 1.6, n), 2),
    })


def load_demo(days: int = 10) -> DataStatus:
    return DataStatus(
        source=DEMO, df=make_demo(days), ok=True,
        headline="DEMO DATA - synthetic, not from the weather station",
        detail="No live or cached Conduit data was available, so the dashboard "
               "is showing generated numbers to demonstrate behaviour.",
    )


# --------------------------------------------------------------------------
# The one the app calls
# --------------------------------------------------------------------------

def load_observations(
    *,
    days: int = 7,
    allow_live: bool = True,
    allow_demo: bool = True,
) -> DataStatus:
    """Live, else cached, else demo. Records every attempt for the banner."""
    attempts: list[str] = []

    if allow_live:
        live = load_live(days=days)
        if live.ok:
            live.attempts = attempts
            return live
        attempts.append(f"Live station: {live.detail}")

    cached = load_cached()
    if cached.ok:
        cached.attempts = attempts
        return cached
    attempts.append(f"Cached history: {cached.detail}")

    if not allow_demo:
        return DataStatus(source=CACHED, ok=False, attempts=attempts,
                          detail="no real data available")

    demo = load_demo(days=days)
    demo.attempts = attempts
    return demo
