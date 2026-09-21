"""Cross-check our reconstructed rainfall against an independent source.

We derive daily rainfall by differencing the station's running total (rg1tt),
having found the per-interval field rg1 unusable. That reconstruction deserves
an independent check before anyone relies on it.

This compares our daily totals with ERA5 reanalysis, pulled from the Open-Meteo
historical archive for the same coordinates.

WHAT THIS IS AND IS NOT
-----------------------
It is a CREDIBILITY CHECK. If our reconstruction were wrong - double counting,
missing resets, off by an order of magnitude - it would not track a reputable
independent record at all.

It is NOT calibration. We do not adjust station data to match ERA5, and nothing
here feeds back into the engine. ERA5 is a ~9 km reanalysis grid cell; the
station is one point inside it. For convective tropical rainfall the two SHOULD
differ on any given day - a storm can soak one field and miss the next. Close
agreement on daily totals is not expected and would itself be suspicious.

    .venv/Scripts/python.exe analysis/validate_rain.py
    .venv/Scripts/python.exe analysis/validate_rain.py --from 2025-10-01 --to 2025-12-31
"""

from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass, field
from pathlib import Path

import pandas as pd
import requests

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import config  # noqa: E402
from analysis import backtest as bt  # noqa: E402

ARCHIVE_URL = "https://archive-api.open-meteo.com/v1/archive"

# Above this a day counts as "rainy" for the agreement table. 1 mm is the
# conventional wet-day threshold and is what the brief asked for.
WET_DAY_MM = 1.0

RULE = "=" * 78
SUB = "-" * 78

# Where the day-by-day comparison is cached. Committed to the repo so the
# dashboard can show this panel without a network call.
VALIDATION_CSV_NAME = "rain_validation.csv"

REQUIRED_COLUMNS = ("date", "station_mm", "era5_mm")


@dataclass
class ValidationData:
    """The station-vs-ERA5 comparison, and where it came from."""

    df: pd.DataFrame = field(default_factory=pd.DataFrame)
    ok: bool = False
    source: str = "unavailable"   # "cache" | "fetched" | "unavailable"
    note: str = ""
    error: str | None = None


def summarise_comparison(df: pd.DataFrame) -> dict:
    """Every headline number in one place.

    The dashboard panel, the CLI report and the README all quote these, so they
    are computed once rather than three times in three slightly different ways.
    """
    s, e = df["station_mm"], df["era5_mm"]
    s_wet, e_wet = s > WET_DAY_MM, e > WET_DAY_MM
    both = int((s_wet & e_wet).sum())
    only_station = int((s_wet & ~e_wet).sum())
    only_era5 = int((~s_wet & e_wet).sum())
    neither = int((~s_wet & ~e_wet).sum())
    agree = both + neither
    n = len(df)

    out = {
        "days": n,
        "station_total": float(s.sum()),
        "era5_total": float(e.sum()),
        "ratio": float(s.sum() / e.sum()) if e.sum() else float("nan"),
        "station_mean": float(s.mean()) if n else float("nan"),
        "era5_mean": float(e.mean()) if n else float("nan"),
        "both_wet": both,
        "only_station": only_station,
        "only_era5": only_era5,
        "neither_wet": neither,
        "agree_days": agree,
        "agree_pct": 100 * agree / n if n else float("nan"),
        "jaccard": both / (both + only_station + only_era5)
                   if (both + only_station + only_era5) else float("nan"),
        "pearson": float("nan"),
        "spearman": float("nan"),
    }
    if n >= 3 and s.std() > 0 and e.std() > 0:
        out["pearson"] = float(s.corr(e))
        # Spearman = Pearson on ranks; computed directly to avoid needing scipy.
        out["spearman"] = float(s.rank().corr(e.rank()))
    return out


def load_comparison(path: Path | None = None, *,
                    allow_fetch: bool = True) -> ValidationData:
    """Load the cached comparison; rebuild it only if the file is missing.

    Never raises. If the cache is absent and Open-Meteo cannot be reached, the
    caller gets ok=False and a reason to show, not an exception.
    """
    path = path or (config.DATA_DIR / VALIDATION_CSV_NAME)

    if path.exists():
        try:
            df = pd.read_csv(path)
        except Exception as exc:  # noqa: BLE001 - fall through to a rebuild
            df = None
            cache_error = f"could not read {path.name}: {exc}"
        else:
            cache_error = None
            missing = [c for c in REQUIRED_COLUMNS if c not in df.columns]
            if missing:
                cache_error = f"{path.name} is missing {missing}"
            elif df.empty:
                cache_error = f"{path.name} is empty"
            else:
                return ValidationData(
                    df=df, ok=True, source="cache",
                    note=f"from {path.name} ({len(df)} days)",
                )
    else:
        cache_error = f"{path.name} not found"

    if not allow_fetch:
        return ValidationData(ok=False, error=cache_error)

    # Rebuild from the station history plus a fresh ERA5 pull.
    hist, _ = bt.load_history()
    if hist.empty or "rain_mm" not in hist.columns:
        return ValidationData(
            ok=False,
            error=f"{cache_error}; no station history to rebuild from")

    daily = station_daily(hist)
    era5 = fetch_era5(str(daily["date"].min()), str(daily["date"].max()))
    if era5.empty:
        return ValidationData(
            ok=False,
            error=f"{cache_error}; ERA5 archive unreachable")

    merged = daily.merge(era5, on="date", how="inner").dropna()
    if merged.empty:
        return ValidationData(
            ok=False, error=f"{cache_error}; no overlapping days")

    return ValidationData(df=merged, ok=True, source="fetched",
                          note=f"rebuilt from the ERA5 archive ({len(merged)} days)")


def fetch_era5(start: str, end: str) -> pd.DataFrame:
    """Daily precipitation from ERA5 via the Open-Meteo archive. No key needed."""
    params = {
        "latitude": config.JKUAT_LAT,
        "longitude": config.JKUAT_LON,
        "start_date": start,
        "end_date": end,
        "daily": "precipitation_sum",
        "timezone": "Africa/Nairobi",
    }
    try:
        resp = requests.get(ARCHIVE_URL, params=params, timeout=60,
                            headers={"User-Agent": config.USER_AGENT})
    except requests.exceptions.RequestException as exc:
        print(f"  ERA5 request failed: {exc}")
        return pd.DataFrame()

    if resp.status_code != 200:
        print(f"  ERA5 HTTP {resp.status_code}: {resp.text[:200]}")
        return pd.DataFrame()

    payload = resp.json()
    daily = payload.get("daily")
    if not daily or "time" not in daily:
        print("  ERA5 returned no daily block")
        return pd.DataFrame()

    return pd.DataFrame({
        "date": pd.to_datetime(daily["time"]).date,
        "era5_mm": pd.to_numeric(pd.Series(daily["precipitation_sum"]), errors="coerce"),
    })


def station_daily(df: pd.DataFrame) -> pd.DataFrame:
    """Our reconstructed rainfall, summed per local calendar day."""
    d = df.dropna(subset=["timestamp"]).copy()
    d["date"] = d["timestamp"].dt.date
    out = d.groupby("date")["rain_mm"].sum().reset_index()
    return out.rename(columns={"rain_mm": "station_mm"})


def compare(merged: pd.DataFrame) -> None:
    """Print the CLI report. Numbers come from summarise_comparison() so the
    report and the dashboard panel can never quote different figures."""
    st = summarise_comparison(merged)
    s, e = merged["station_mm"], merged["era5_mm"]

    print()
    print(RULE)
    print("DAILY RAINFALL: STATION (reconstructed from rg1tt) vs ERA5")
    print(RULE)
    print(f"  days compared        : {st['days']}")
    print(f"  station total        : {st['station_total']:8.1f} mm")
    print(f"  ERA5 total           : {st['era5_total']:8.1f} mm")
    print(f"  ratio station/ERA5   : {st['ratio']:8.2f}")
    print(f"  station mean/day     : {st['station_mean']:8.2f} mm")
    print(f"  ERA5 mean/day        : {st['era5_mean']:8.2f} mm")

    print()
    print("  Correlation of daily totals:")
    if st["pearson"] == st["pearson"]:      # not NaN
        print(f"    Pearson  r = {st['pearson']:.3f}")
        print(f"    Spearman r = {st['spearman']:.3f}   (rank - less swayed by one big storm)")
    else:
        print("    not enough variation to correlate")

    print()
    print(f"  Rainy-day agreement (a day counts as rainy above {WET_DAY_MM:.0f} mm):")
    print(f"    both rainy         : {st['both_wet']:>4}")
    print(f"    station only       : {st['only_station']:>4}")
    print(f"    ERA5 only          : {st['only_era5']:>4}")
    print(f"    both dry           : {st['neither_wet']:>4}")
    print(f"    agree              : {st['agree_days']:>4} / {st['days']}  ({st['agree_pct']:.0f}%)")
    print(f"    Jaccard (rainy days) : {st['jaccard']:.2f}")

    print()
    print("  Largest disagreements:")
    merged = merged.assign(diff=(s - e).abs())
    for _, r in merged.nlargest(5, "diff").iterrows():
        print(f"    {r['date']}  station {r['station_mm']:6.1f} mm   "
              f"ERA5 {r['era5_mm']:6.1f} mm   diff {r['diff']:6.1f}")

    print()
    print(SUB)
    print("  HOW TO READ THIS")
    print(SUB)
    print("  ERA5 is a REANALYSIS - a model reconstruction that assimilates")
    print("  satellite and ground observations - on a ~9 km grid. It is not a")
    print("  direct satellite measurement. The station is a single point inside")
    print("  one of those grid cells, and rainfall here is convective, so a storm")
    print("  can soak one field and miss the next. Daily totals SHOULD differ.")
    print("  The seasonal total agreeing is the meaningful result. Nothing here")
    print("  is fed back into our data; this is a credibility check, not a")
    print("  calibration.")


def main() -> int:
    ap = argparse.ArgumentParser(description="Cross-check rainfall against ERA5")
    ap.add_argument("--from", dest="start", default=None)
    ap.add_argument("--to", dest="end", default=None)
    args = ap.parse_args()

    print(RULE)
    print("SHAMBA PULSE - RAINFALL CROSS-CHECK")
    print(RULE)

    hist, source = bt.load_history()
    if hist.empty:
        print("No history found. Run analysis/fetch_history.py first.")
        return 1
    if "rain_mm" not in hist.columns:
        print("History has no rain_mm column.")
        return 1

    daily = station_daily(hist)
    start = args.start or str(daily["date"].min())
    end = args.end or str(daily["date"].max())
    print(f"\nStation source: {source}")
    print(f"Period: {start} -> {end}")

    print("\nFetching ERA5 from the Open-Meteo archive...")
    era5 = fetch_era5(start, end)
    if era5.empty:
        print("Could not fetch ERA5; skipping the comparison.")
        return 1

    merged = daily.merge(era5, on="date", how="inner").dropna()
    if merged.empty:
        print("No overlapping days to compare.")
        return 1

    compare(merged)

    out = config.DATA_DIR / "rain_validation.csv"
    merged.to_csv(out, index=False)
    print(f"\nWrote daily comparison -> {out}")

    print()
    print(RULE)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
