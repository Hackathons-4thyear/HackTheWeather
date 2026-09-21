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
    n = len(merged)
    s, e = merged["station_mm"], merged["era5_mm"]

    print()
    print(RULE)
    print("DAILY RAINFALL: STATION (reconstructed from rg1tt) vs ERA5")
    print(RULE)
    print(f"  days compared        : {n}")
    print(f"  station total        : {s.sum():8.1f} mm")
    print(f"  ERA5 total           : {e.sum():8.1f} mm")
    if e.sum():
        print(f"  ratio station/ERA5   : {s.sum() / e.sum():8.2f}")
    print(f"  station mean/day     : {s.mean():8.2f} mm")
    print(f"  ERA5 mean/day        : {e.mean():8.2f} mm")

    print()
    print("  Correlation of daily totals:")
    if n >= 3 and s.std() > 0 and e.std() > 0:
        pearson = s.corr(e)
        # Spearman = Pearson on the ranks. Computed directly so we do not pull
        # in scipy for one statistic.
        spearman = s.rank().corr(e.rank())
        print(f"    Pearson  r = {pearson:.3f}")
        print(f"    Spearman r = {spearman:.3f}   (rank - less swayed by one big storm)")
    else:
        print("    not enough variation to correlate")

    # Wet-day agreement.
    s_wet, e_wet = s > WET_DAY_MM, e > WET_DAY_MM
    both = int((s_wet & e_wet).sum())
    only_station = int((s_wet & ~e_wet).sum())
    only_era5 = int((~s_wet & e_wet).sum())
    neither = int((~s_wet & ~e_wet).sum())

    print()
    print(f"  Rainy-day agreement (a day counts as rainy above {WET_DAY_MM:.0f} mm):")
    print(f"    both rainy         : {both:>4}")
    print(f"    station only       : {only_station:>4}")
    print(f"    ERA5 only          : {only_era5:>4}")
    print(f"    both dry           : {neither:>4}")
    agree = both + neither
    print(f"    agree              : {agree:>4} / {n}  ({100 * agree / n:.0f}%)")

    if both + only_station + only_era5:
        jaccard = both / (both + only_station + only_era5)
        print(f"    Jaccard (rainy days) : {jaccard:.2f}")

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
    print("  ERA5 is a ~9 km reanalysis grid cell; the station is one point")
    print("  inside it. Tropical rainfall here is convective, so a storm can")
    print("  soak one field and miss the next. Daily totals SHOULD differ.")
    print("  What matters is that the two track each other - that wet spells")
    print("  line up and the seasonal totals are the same order of magnitude.")
    print("  Nothing here is fed back into our data; this is a credibility")
    print("  check, not a calibration.")


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
