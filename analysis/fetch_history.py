"""Download the full available history from the Conduit station.

Strategy:
  1. Probe backwards from a seed date to find the earliest day with data.
  2. Walk forward in chunks (7 days by default), shrinking to 1-day chunks when
     a chunk fails or comes back suspiciously large.
  3. Cache every chunk as data/raw/conduit_<from>_<to>.json so re-runs are cheap
     and we never hammer the organizers' server twice for the same range.
  4. Combine into data/conduit_history.parquet (+ a CSV copy), de-duplicated by
     timestamp and sorted.

Politeness matters here - this is a shared hackathon resource. We sleep between
requests, retry with exponential backoff, and identify ourselves via User-Agent.

    .venv/Scripts/python.exe analysis/fetch_history.py
    .venv/Scripts/python.exe analysis/fetch_history.py --start 2025-06-01
    .venv/Scripts/python.exe analysis/fetch_history.py --no-probe --start 2025-10-01
    .venv/Scripts/python.exe analysis/fetch_history.py --rebuild   # combine cache only
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import date, timedelta
from pathlib import Path
from typing import Any

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import config  # noqa: E402
from services import conduit_api  # noqa: E402
from services import data_processor as dp  # noqa: E402

RAW_DIR = config.DATA_DIR / "raw"
HISTORY_PARQUET = config.DATA_DIR / "conduit_history.parquet"
HISTORY_CSV = config.DATA_DIR / "conduit_history.csv"

# The organizers' own example uses 2025-06-01, so the station was live by then.
DEFAULT_SEED_DATE = date(2025, 6, 1)

# A single 7-day chunk at 15-min cadence is ~672 rows. If a chunk returns far
# more than that the server may be ignoring our date filter, so we shrink.
ROWS_PER_DAY_EXPECTED = 24 * 60 / config.STATION_INTERVAL_MINUTES  # 96
CHUNK_ROW_SANITY_FACTOR = 3

RULE = "=" * 78


# --------------------------------------------------------------------------
# Cache
# --------------------------------------------------------------------------

def chunk_path(start: date, end: date) -> Path:
    return RAW_DIR / f"conduit_{start.isoformat()}_{end.isoformat()}.json"


def load_cached(start: date, end: date) -> list[dict[str, Any]] | None:
    """Return cached rows for this exact range, or None if not cached."""
    path = chunk_path(start, end)
    if not path.exists():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None  # Corrupt cache entry - just refetch.
    return payload if isinstance(payload, list) else None


def save_cached(start: date, end: date, rows: list[dict[str, Any]]) -> None:
    RAW_DIR.mkdir(parents=True, exist_ok=True)
    chunk_path(start, end).write_text(
        json.dumps(rows, indent=1, default=str), encoding="utf-8"
    )


# --------------------------------------------------------------------------
# Fetching
# --------------------------------------------------------------------------

def fetch_chunk(
    start: date,
    end: date,
    *,
    use_cache: bool = True,
    max_retries: int | None = None,
) -> tuple[list[dict[str, Any]] | None, str]:
    """Fetch one date range, with cache, retries and backoff.

    Returns (rows, status) where status is one of:
    'cached', 'ok', 'empty', or 'failed: <reason>'.
    """
    max_retries = max_retries or config.CONDUIT_MAX_RETRIES

    if use_cache:
        cached = load_cached(start, end)
        if cached is not None:
            return cached, "cached"

    last_error = "unknown"
    for attempt in range(1, max_retries + 1):
        result = conduit_api.fetch_range(start, end)

        if result.ok:
            save_cached(start, end, result.rows)
            return result.rows, "ok" if result.rows else "empty"

        last_error = result.error or "unknown"

        # An auth failure will never fix itself - stop retrying immediately.
        if result.status_code in (401, 403):
            return None, f"failed: {last_error}"

        if attempt < max_retries:
            backoff = config.CONDUIT_REQUEST_DELAY_SECONDS * (2 ** attempt)
            print(f"      retry {attempt}/{max_retries - 1} in {backoff:.0f}s ({last_error})")
            time.sleep(backoff)

    return None, f"failed: {last_error}"


def probe_earliest(seed: date, *, max_back_steps: int = 24) -> date | None:
    """Walk backwards from `seed` in month-sized steps to find where data starts.

    Cheap approach: sample a single day per step. The station logs every 15 min,
    so any day with the station running returns rows.
    """
    print(f"\nProbing backwards from {seed} to find the earliest data...")
    earliest_hit: date | None = None
    probe = seed

    for step in range(max_back_steps):
        rows, status = fetch_chunk(probe, probe)
        n = len(rows) if rows else 0
        print(f"  {probe}  ->  {n:>4} rows  [{status}]")

        if status.startswith("failed"):
            print(f"  aborting probe: {status}")
            break

        if n > 0:
            earliest_hit = probe
            probe = probe - timedelta(days=30)
        else:
            # Empty day. Could be an outage rather than the true start, so try
            # a couple more steps back before concluding we are past the start.
            probe = probe - timedelta(days=30)
            if step > 2 and earliest_hit is not None:
                break

        time.sleep(config.CONDUIT_REQUEST_DELAY_SECONDS)

    if earliest_hit is None:
        return None

    # Narrow down: walk forward day by day from 30 days before the earliest hit.
    print(f"\n  Narrowing around {earliest_hit}...")
    cursor = earliest_hit - timedelta(days=30)
    while cursor < earliest_hit:
        rows, status = fetch_chunk(cursor, cursor)
        if rows:
            print(f"  {cursor}  ->  {len(rows)} rows - earlier data found")
            earliest_hit = cursor
            break
        cursor += timedelta(days=5)
        time.sleep(config.CONDUIT_REQUEST_DELAY_SECONDS)

    # Step back one day at a time to pin the exact first day. Without this the
    # 5-day sweep above can overshoot the true start by up to 4 days. Three
    # consecutive empty days ends the walk, so a short outage near the start
    # does not stop us short.
    probe = earliest_hit - timedelta(days=1)
    misses = 0
    while misses < 3:
        rows, status = fetch_chunk(probe, probe)
        if status.startswith("failed"):
            break
        if rows:
            earliest_hit = probe
            misses = 0
            print(f"  {probe}  ->  {len(rows)} rows - earlier still")
        else:
            misses += 1
        probe -= timedelta(days=1)
        time.sleep(config.CONDUIT_REQUEST_DELAY_SECONDS)

    return earliest_hit


def fetch_history(
    start: date,
    end: date,
    *,
    chunk_days: int | None = None,
    use_cache: bool = True,
) -> list[dict[str, Any]]:
    """Fetch [start, end] in chunks, shrinking to 1 day when a chunk misbehaves."""
    chunk_days = chunk_days or config.CONDUIT_CHUNK_DAYS
    all_rows: list[dict[str, Any]] = []
    failures: list[tuple[date, date, str]] = []

    cursor = start
    total_days = (end - start).days + 1
    print(f"\nFetching {total_days} days ({start} -> {end}) in {chunk_days}-day chunks")
    print(RULE)

    while cursor <= end:
        chunk_end = min(cursor + timedelta(days=chunk_days - 1), end)
        rows, status = fetch_chunk(cursor, chunk_end, use_cache=use_cache)

        # Auth failures are fatal - no point grinding through hundreds of chunks.
        if status.startswith("failed") and "APIKey" in status:
            print(f"  FATAL: {status}")
            print("  Check CONDUIT_API_KEY / CONDUIT_EMAIL in .env and re-run.")
            break

        n = len(rows) if rows else 0
        span_days = (chunk_end - cursor).days + 1
        too_big = n > ROWS_PER_DAY_EXPECTED * span_days * CHUNK_ROW_SANITY_FACTOR

        if rows is None or too_big:
            reason = "oversized response" if too_big else status
            if chunk_days > 1:
                print(f"  {cursor} -> {chunk_end}: {reason}; retrying day by day")
                # Re-walk this same span one day at a time.
                day = cursor
                while day <= chunk_end:
                    drows, dstatus = fetch_chunk(day, day, use_cache=use_cache)
                    dn = len(drows) if drows else 0
                    print(f"    {day}  ->  {dn:>4} rows  [{dstatus}]")
                    if drows:
                        all_rows.extend(drows)
                    elif dstatus.startswith("failed"):
                        failures.append((day, day, dstatus))
                    if dstatus != "cached":
                        time.sleep(config.CONDUIT_REQUEST_DELAY_SECONDS)
                    day += timedelta(days=1)
            else:
                print(f"  {cursor}: {reason}")
                failures.append((cursor, chunk_end, status))
        else:
            print(f"  {cursor} -> {chunk_end}: {n:>5} rows  [{status}]")
            all_rows.extend(rows)

        if status != "cached":
            time.sleep(config.CONDUIT_REQUEST_DELAY_SECONDS)
        cursor = chunk_end + timedelta(days=1)

    print(RULE)
    print(f"Collected {len(all_rows):,} raw rows")
    if failures:
        print(f"\n!! {len(failures)} chunk(s) failed:")
        for s, e, why in failures[:10]:
            print(f"   {s} -> {e}: {why}")
    return all_rows


# --------------------------------------------------------------------------
# Combine + report
# --------------------------------------------------------------------------

def combine_cache() -> pd.DataFrame:
    """Rebuild the canonical history from every cached chunk on disk."""
    if not RAW_DIR.exists():
        print(f"No cache directory at {RAW_DIR}")
        return pd.DataFrame()

    files = sorted(RAW_DIR.glob("conduit_*.json"))
    if not files:
        print(f"No cached chunks in {RAW_DIR}")
        return pd.DataFrame()

    print(f"\nCombining {len(files)} cached chunk(s) from {RAW_DIR}")
    frames: list[pd.DataFrame] = []
    for path in files:
        try:
            rows = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            print(f"  skipping {path.name}: {exc}")
            continue
        if isinstance(rows, list) and rows:
            frames.append(pd.DataFrame(rows))

    if not frames:
        return pd.DataFrame()

    raw = pd.concat(frames, ignore_index=True)
    print(f"  {len(raw):,} raw rows, {raw.shape[1]} columns")
    print(f"  raw columns: {list(raw.columns)}")

    canon = dp.to_canonical(raw)
    print(f"  canonical: {len(canon):,} rows after cleaning + de-duplication")
    return canon


def report(df: pd.DataFrame) -> None:
    """Print the summary the team needs to judge data adequacy."""
    print()
    print(RULE)
    print("HISTORY SUMMARY")
    print(RULE)

    if df.empty or "timestamp" not in df.columns:
        print("No usable data.")
        return

    ts = df["timestamp"].dropna().sort_values()
    span = ts.max() - ts.min()
    print(f"  rows          : {len(df):,}")
    print(f"  date range    : {ts.min()}  ->  {ts.max()}")
    print(f"  span          : {span.days} days")

    deltas = ts.diff().dropna()
    if len(deltas):
        modal = deltas.mode()
        print(f"  modal interval: {modal.iloc[0] if len(modal) else 'n/a'} "
              f"(expected {config.STATION_INTERVAL_MINUTES} min)")
        print(f"  median interval: {deltas.median()}")

    # Missing-interval percentage against a perfect 15-minute grid.
    step = pd.Timedelta(minutes=config.STATION_INTERVAL_MINUTES)
    expected = int(span / step) + 1
    missing_pct = 100 * (1 - len(ts) / expected) if expected else 0.0
    print(f"  expected rows : {expected:,} on a perfect {config.STATION_INTERVAL_MINUTES}-min grid")
    print(f"  missing       : {missing_pct:.1f}% of intervals")

    gaps = deltas[deltas > pd.Timedelta(hours=6)]
    print(f"\n  gaps > 6 hours: {len(gaps)}")
    if len(gaps):
        total_gap = gaps.sum()
        print(f"  total lost to those gaps: {total_gap} ({total_gap.days} days)")
        for idx, gap in gaps.sort_values(ascending=False).head(15).items():
            end = ts.loc[idx]
            print(f"    {end - gap}  ->  {end}   ({gap})")

    # Monthly coverage - shows at a glance whether a backtest season is covered.
    print("\n  Coverage by month (rows, % of a full month):")
    per_month = ts.dt.to_period("M").value_counts().sort_index()
    for period, count in per_month.items():
        days_in_month = period.days_in_month
        full = days_in_month * ROWS_PER_DAY_EXPECTED
        bar = "#" * int(30 * min(count / full, 1.0))
        print(f"    {period}  {count:>6,}  {100 * count / full:>5.1f}%  {bar}")


def save(df: pd.DataFrame) -> None:
    if df.empty:
        print("\nNothing to save.")
        return
    config.DATA_DIR.mkdir(parents=True, exist_ok=True)
    df.to_parquet(HISTORY_PARQUET, index=False)
    df.to_csv(HISTORY_CSV, index=False)
    pq_mb = HISTORY_PARQUET.stat().st_size / 1024 / 1024
    csv_mb = HISTORY_CSV.stat().st_size / 1024 / 1024
    print(f"\nSaved:")
    print(f"  {HISTORY_PARQUET}  ({pq_mb:.2f} MB)")
    print(f"  {HISTORY_CSV}  ({csv_mb:.2f} MB)")


def main() -> int:
    ap = argparse.ArgumentParser(description="Download full Conduit station history")
    ap.add_argument("--start", type=date.fromisoformat, default=None,
                    help="First date to fetch (YYYY-MM-DD). Default: probe for it.")
    ap.add_argument("--end", type=date.fromisoformat, default=None,
                    help="Last date to fetch (YYYY-MM-DD). Default: today.")
    ap.add_argument("--seed", type=date.fromisoformat, default=DEFAULT_SEED_DATE,
                    help=f"Where backward probing starts. Default {DEFAULT_SEED_DATE}.")
    ap.add_argument("--chunk-days", type=int, default=config.CONDUIT_CHUNK_DAYS)
    ap.add_argument("--no-probe", action="store_true",
                    help="Skip the earliest-date probe; requires --start.")
    ap.add_argument("--no-cache", action="store_true",
                    help="Ignore cached chunks and refetch everything.")
    ap.add_argument("--rebuild", action="store_true",
                    help="Do not fetch; just recombine what is already cached.")
    args = ap.parse_args()

    print(RULE)
    print("SHAMBA PULSE - CONDUIT HISTORY DOWNLOAD")
    print(RULE)

    if args.rebuild:
        df = combine_cache()
        report(df)
        save(df)
        return 0 if not df.empty else 1

    if not config.CONDUIT_API_KEY or not config.CONDUIT_EMAIL:
        print("\nCONDUIT_API_KEY / CONDUIT_EMAIL missing - set them in .env first.")
        return 1

    end = args.end or date.today()

    if args.start:
        start = args.start
    elif args.no_probe:
        print("\n--no-probe requires --start")
        return 1
    else:
        start = probe_earliest(args.seed)
        if start is None:
            print("\nProbe found no data. Try --start explicitly.")
            return 1
        print(f"\nEarliest data found: {start}")

    fetch_history(start, end, chunk_days=args.chunk_days, use_cache=not args.no_cache)

    df = combine_cache()
    report(df)
    save(df)
    return 0 if not df.empty else 1


if __name__ == "__main__":
    raise SystemExit(main())
