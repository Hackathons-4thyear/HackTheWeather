"""Inspect whatever historical Conduit data is sitting in data/.

Makes no assumptions about column names. For every CSV / XLSX / JSON file found
it reports: columns and dtypes, the date range, the actual sampling interval,
missing-value counts, and per-variable stats. Rainfall columns get a
zero-inflation summary instead of a misleading mean.

It finishes by running the candidate column map from data_processor.py against
the real columns, so we can see what mapped, what did not, and what is missing.

    .venv/Scripts/python.exe analysis/explore_data.py
    .venv/Scripts/python.exe analysis/explore_data.py data/somefile.csv
"""

from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import config  # noqa: E402
from services import data_processor as dp  # noqa: E402

pd.set_option("display.width", 200)
pd.set_option("display.max_columns", 60)

RULE = "=" * 78
SUB = "-" * 78


def find_data_files(explicit: list[str]) -> list[Path]:
    if explicit:
        return [Path(p) for p in explicit]
    if not config.DATA_DIR.exists():
        return []
    files: list[Path] = []
    for pattern in ("*.csv", "*.xlsx", "*.xls", "*.json"):
        files.extend(sorted(config.DATA_DIR.glob(pattern)))
    return files


def load_any(path: Path) -> pd.DataFrame:
    """Load CSV / XLSX / JSON into a DataFrame without interpreting anything."""
    suffix = path.suffix.lower()
    if suffix == ".csv":
        return pd.read_csv(path, low_memory=False)
    if suffix in {".xlsx", ".xls"}:
        return pd.read_excel(path)
    if suffix == ".json":
        return pd.read_json(path)
    raise ValueError(f"unsupported file type: {suffix}")


def describe_timestamps(df: pd.DataFrame):
    """Find and parse the timestamp column, whatever it is called."""
    ts_col = dp.guess_timestamp_column(df)
    if ts_col is None:
        print("  !! No timestamp-like column found. Columns are:")
        print(f"     {list(df.columns)}")
        return None

    parsed = dp.parse_timestamps(df[ts_col])
    n_bad = int(parsed.isna().sum())

    print(f"  timestamp column : {ts_col!r}")
    print(f"  parsed           : {len(parsed) - n_bad}/{len(parsed)} ({n_bad} unparseable)")
    if n_bad == len(parsed):
        return None

    valid = parsed.dropna().sort_values()
    span = valid.max() - valid.min()
    print(f"  date range       : {valid.min()}  ->  {valid.max()}")
    print(f"  span             : {span.days} days ({span})")

    # Actual sampling interval, from the gaps between consecutive readings.
    deltas = valid.diff().dropna()
    if len(deltas):
        modal = deltas.mode()
        modal_str = str(modal.iloc[0]) if len(modal) else "n/a"
        print(f"  modal interval   : {modal_str}  (expected {config.STATION_INTERVAL_MINUTES} min)")
        print(f"  median interval  : {deltas.median()}")
        gap_limit = pd.Timedelta(minutes=config.STATION_INTERVAL_MINUTES * 3)
        gaps = deltas[deltas > gap_limit]
        print(f"  gaps > {gap_limit} : {len(gaps)}")
        if len(gaps):
            print(f"    largest gap    : {gaps.max()}")
            for idx, gap in gaps.sort_values(ascending=False).head(3).items():
                end = valid.loc[idx]
                print(f"      {end - gap}  ->  {end}   ({gap})")

    # Expected vs actual coverage.
    if len(valid) > 1:
        expected = span / pd.Timedelta(minutes=config.STATION_INTERVAL_MINUTES) + 1
        pct = 100 * len(valid) / expected
        print(f"  coverage         : {len(valid)} rows of ~{expected:.0f} expected ({pct:.1f}%)")
    return parsed


def describe_columns(df: pd.DataFrame) -> None:
    print()
    print(SUB)
    print("  PER-COLUMN SUMMARY")
    print(SUB)
    rows = []
    for col in df.columns:
        s = df[col]
        coerced = pd.to_numeric(s, errors="coerce")
        numericish = coerced.notna().sum() > 0.5 * len(s)
        entry = {
            "column": col,
            "dtype": str(s.dtype),
            "non_null": int(s.notna().sum()),
            "missing": int(s.isna().sum()),
            "miss_pct": round(100 * s.isna().mean(), 1),
            "n_unique": int(s.nunique(dropna=True)),
        }
        if numericish and coerced.notna().any():
            entry.update(
                vmin=round(float(coerced.min()), 3),
                median=round(float(coerced.median()), 3),
                vmax=round(float(coerced.max()), 3),
                zeros=int((coerced == 0).sum()),
            )
        else:
            entry.update(vmin=None, median=None, vmax=None, zeros=None)
        rows.append(entry)

    summary = pd.DataFrame(rows)
    print(summary.to_string(index=False))

    # Constant / all-null columns are dead sensors - worth calling out.
    dead = summary[summary["n_unique"] <= 1]["column"].tolist()
    if dead:
        print()
        print(f"  !! Constant or empty columns (dead sensors?): {dead}")


def describe_rainfall(df: pd.DataFrame) -> None:
    """Rainfall is zero-inflated - a mean is meaningless, so report it properly."""
    rain_cols = [c for c in df.columns if dp.looks_like_rain(c)]
    if not rain_cols:
        print()
        print("  (no rainfall-looking columns found)")
        return

    print()
    print(SUB)
    print("  RAINFALL (zero-inflated - reported as wet/dry, not as a mean)")
    print(SUB)
    for col in rain_cols:
        s = pd.to_numeric(df[col], errors="coerce")
        n = int(s.notna().sum())
        if n == 0:
            print(f"  {col}: no numeric values")
            continue
        wet = s > 0
        n_wet = int(wet.sum())
        print(f"  {col}:")
        print(f"    readings      : {n}")
        print(f"    zero / dry    : {n - n_wet} ({100 * (n - n_wet) / n:.1f}%)")
        print(f"    non-zero / wet: {n_wet} ({100 * n_wet / n:.1f}%)")
        print(f"    total         : {s.sum():.2f}")
        if n_wet:
            wv = s[wet]
            print(f"    wet readings  : min {wv.min():.3f}  median {wv.median():.3f}  max {wv.max():.3f}")
            qs = "  ".join(f"p{int(q * 100)}={wv.quantile(q):.3f}" for q in (0.5, 0.9, 0.99))
            print(f"    wet quantiles : {qs}")


def describe_humidity_and_temp(df: pd.DataFrame, ts) -> None:
    """The two variables the Hutton criteria depend on - check they are usable."""
    print()
    print(SUB)
    print("  HUTTON INPUTS (temperature + humidity)")
    print(SUB)

    hum_cols = [c for c in df.columns if dp.looks_like_humidity(c)]
    temp_cols = [c for c in df.columns if dp.looks_like_temperature(c)]

    print(f"  humidity candidates   : {hum_cols or 'NONE FOUND'}")
    print(f"  temperature candidates: {temp_cols or 'NONE FOUND'}")

    for col in hum_cols:
        s = pd.to_numeric(df[col], errors="coerce").dropna()
        if s.empty:
            continue
        over90 = int((s >= config.HUTTON_RH_THRESHOLD_PCT).sum())
        print()
        print(f"  {col}: range {s.min():.1f}-{s.max():.1f} %")
        print(f"    readings >= {config.HUTTON_RH_THRESHOLD_PCT:.0f}% RH: "
              f"{over90} ({100 * over90 / len(s):.1f}%)")
        bad = int(((s < 0) | (s > 100)).sum())
        if bad:
            print(f"    !! {bad} readings outside 0-100% - needs cleaning")

    for col in temp_cols:
        s = pd.to_numeric(df[col], errors="coerce").dropna()
        if s.empty:
            continue
        print()
        print(f"  {col}: range {s.min():.1f}-{s.max():.1f} degC  (median {s.median():.1f})")
        below = int((s < config.HUTTON_MIN_TEMP_C).sum())
        print(f"    readings < {config.HUTTON_MIN_TEMP_C:.0f} degC: {below} ({100 * below / len(s):.1f}%)")
        bad = int(((s < -10) | (s > 60)).sum())
        if bad:
            print(f"    !! {bad} implausible readings (< -10 or > 60 degC)")

    print()
    if ts is not None and hum_cols and temp_cols:
        print("  -> Both Hutton inputs present. Daily Hutton evaluation is possible.")
    else:
        print("  -> !! Missing an input the Hutton criteria need.")


def report_column_mapping(df: pd.DataFrame) -> None:
    """Run the canonical mapping and show exactly what resolved."""
    print()
    print(SUB)
    print("  CANONICAL COLUMN MAPPING (services/data_processor.py)")
    print(SUB)
    mapping = dp.build_column_map(df.columns)

    print("  canonical name          <- raw column")
    for canon in dp.CANONICAL_COLUMNS:
        raw = mapping.get(canon)
        marker = "   " if raw else " ! "
        print(f"  {marker}{canon:<22} <- {raw if raw else '(NOT FOUND)'}")
    if "rain_mm_2" in mapping:
        print(f"     {'rain_mm_2 (backup)':<22} <- {mapping['rain_mm_2']}")

    mapped_raw = set(mapping.values())
    unmapped = [c for c in df.columns if c not in mapped_raw]
    if unmapped:
        print()
        print(f"  Unmapped raw columns ({len(unmapped)}):")
        for c in unmapped:
            print(f"    - {c}")
        print("  (add aliases to data_processor.COLUMN_ALIASES if any of these matter)")

    missing = [c for c in dp.REQUIRED_COLUMNS if c not in mapping]
    print()
    if missing:
        print(f"  !! MISSING REQUIRED for the engine: {missing}")
    else:
        print("  All engine-critical columns resolved.")


def summarise_canonical(df: pd.DataFrame) -> None:
    """Run the real conversion and show the cleaned result."""
    print()
    print(SUB)
    print("  CANONICAL FRAME (after cleaning + implausible-value removal)")
    print(SUB)
    try:
        canon = dp.to_canonical(df)
    except Exception as exc:  # noqa: BLE001 - exploration tool
        print(f"  to_canonical FAILED: {type(exc).__name__}: {exc}")
        return

    print(f"  shape: {canon.shape[0]:,} rows x {canon.shape[1]} columns")
    print(f"  columns: {list(canon.columns)}")
    missing = dp.check_required(canon)
    if missing:
        print(f"  !! empty/missing after cleaning: {missing}")
    print()
    print(canon.head(3).to_string())

    hourly = dp.to_hourly(canon)
    print()
    print(f"  hourly resample: {hourly.shape[0]:,} rows")
    print(hourly.head(3).to_string())


def explore(path: Path) -> None:
    print()
    print(RULE)
    print(f"FILE: {path}")
    print(f"size: {path.stat().st_size / 1024:,.1f} KB")
    print(RULE)

    try:
        df = load_any(path)
    except Exception as exc:  # noqa: BLE001 - exploration tool
        print(f"  FAILED to load: {type(exc).__name__}: {exc}")
        return

    print()
    print(f"  shape: {df.shape[0]:,} rows x {df.shape[1]} columns")
    print(f"  columns: {list(df.columns)}")

    print()
    print(SUB)
    print("  TIMESTAMPS")
    print(SUB)
    ts = describe_timestamps(df)

    describe_columns(df)
    describe_rainfall(df)
    describe_humidity_and_temp(df, ts)
    report_column_mapping(df)
    summarise_canonical(df)

    print()
    print(SUB)
    print("  FIRST 3 RAW ROWS")
    print(SUB)
    print(df.head(3).to_string())


def main() -> int:
    files = find_data_files(sys.argv[1:])

    print(RULE)
    print("SHAMBA PULSE - DATA EXPLORATION")
    print(RULE)
    print(f"data dir: {config.DATA_DIR}")

    if not files:
        print()
        print("No data files found.")
        print()
        print("Drop the historical Conduit export into data/ as .csv or .xlsx,")
        print("or run analysis/test_conduit_live.py to pull a range from the API.")
        print()
        print("This script accepts .csv, .xlsx, .xls and .json.")
        return 1

    print(f"found {len(files)} file(s): {[f.name for f in files]}")
    for path in files:
        explore(path)

    print()
    print(RULE)
    print("DONE")
    print(RULE)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
