"""Replay historical Conduit data and show which alerts WOULD have fired.

This is the proof that the engine works on real weather rather than on a
hand-picked demo day.

NO LOOKAHEAD. At each simulated alert time the engine sees only observations
strictly up to that moment. Using the whole record to judge a past day would
make the backtest meaningless, so the slicing is done once, here, and asserted
in tests.

Spray windows are a separate matter. We have no archive of what the forecast
SAID on a past day, only what the weather actually DID. So spray windows in the
backtest are computed from subsequent observed weather and are labelled
PERFECT-FORESIGHT throughout - they show what the ideal advice would have been,
not what the app would have said at the time. Disease alerts carry no such
caveat: they use past observations only, exactly as they would live.

    .venv/Scripts/python.exe analysis/backtest.py
    .venv/Scripts/python.exe analysis/backtest.py --alert-hour 6 --all-days
    .venv/Scripts/python.exe analysis/backtest.py --from 2025-10-01 --to 2025-12-31
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import config  # noqa: E402
from services import alerts as alerts_mod  # noqa: E402
from services import data_processor as dp  # noqa: E402
from services import disease_engine as de  # noqa: E402
from services import spray_window as sw  # noqa: E402

RULE = "=" * 78
SUB = "-" * 78

# Farmers act in the morning, so that is when a daily alert would go out.
DEFAULT_ALERT_HOUR = 6

# The engine needs some history behind the first alert for the Hutton run.
WARMUP_DAYS = 3


def load_history(path: Path | None = None) -> tuple[pd.DataFrame, str]:
    """Load the canonical history. Returns (frame, source description)."""
    if path is not None:
        candidates = [path]
    else:
        candidates = [
            config.DATA_DIR / "conduit_history.parquet",
            config.DATA_DIR / "conduit_history.csv",
        ]
        candidates += sorted(config.DATA_DIR.glob("*.csv"))
        candidates += sorted(config.DATA_DIR.glob("*.xlsx"))

    for cand in candidates:
        if not cand.exists():
            continue
        if cand.suffix == ".parquet":
            df = pd.read_parquet(cand)
        elif cand.suffix == ".csv":
            df = pd.read_csv(cand, low_memory=False)
        else:
            df = pd.read_excel(cand)

        try:
            if "timestamp" in df.columns and "humidity_pct" in df.columns:
                df["timestamp"] = pd.to_datetime(df["timestamp"], errors="coerce")
                canon = df.dropna(subset=["timestamp"])
            else:
                canon = dp.to_canonical(df)   # raw export - map it
        except Exception:  # noqa: BLE001 - not a weather file; try the next
            continue

        # data/ also holds our own outputs (backtest results, the rain
        # validation table). Those glob in ahead of conduit_history.csv
        # alphabetically, so check this really is weather data before using it.
        if canon.empty or dp.check_required(canon):
            continue

        return canon, str(cand)

    return pd.DataFrame(), "(no data found)"


def run_backtest(
    df: pd.DataFrame,
    *,
    alert_hour: int = DEFAULT_ALERT_HOUR,
    start: pd.Timestamp | None = None,
    end: pd.Timestamp | None = None,
    with_spray: bool = True,
) -> pd.DataFrame:
    """Walk the history one day at a time, assessing as the app would have.

    Returns one row per simulated alert time.
    """
    if df.empty or "timestamp" not in df.columns:
        return pd.DataFrame()

    hist = df.sort_values("timestamp").reset_index(drop=True)
    ts = hist["timestamp"]

    first = ts.min().normalize() + pd.Timedelta(days=WARMUP_DAYS)
    last = ts.max().normalize()
    if start is not None:
        first = max(first, pd.Timestamp(start).normalize())
    if end is not None:
        last = min(last, pd.Timestamp(end).normalize())

    # One policy instance walks the whole season, carrying state day to day.
    policy = alerts_mod.AlertPolicy()

    rows: list[dict] = []
    day = first
    while day <= last:
        as_of = day + pd.Timedelta(hours=alert_hour)

        # THE NO-LOOKAHEAD LINE. Everything downstream sees only this slice.
        past = hist[hist["timestamp"] <= as_of]
        if past.empty:
            day += pd.Timedelta(days=1)
            continue

        risk = de.assess(past)

        advice = None
        if with_spray:
            # Perfect-foresight proxy: the weather that actually followed.
            # Labelled as such everywhere it surfaces.
            future = hist[hist["timestamp"] > as_of]
            if not future.empty:
                hourly = dp.to_hourly(future)
                advice = sw.find_windows(hourly, now=as_of)

        alert = alerts_mod.build_alert(risk, advice, now=as_of)
        decision = policy.evaluate(alert, as_of)

        rows.append({
            "as_of": as_of,
            "date": day.date(),
            "level": risk.level,
            "hutton_level": risk.hutton_level,
            "humid_hours_level": risk.humid_hours_level,
            "consecutive_hutton_days": risk.consecutive_hutton_days,
            "humid_hours_24h": risk.humid_hours_last_24h,
            "method": risk.method,
            # eligible = the level alone warrants a text (the old behaviour)
            # would_send = what the send POLICY actually decides
            "eligible": alert.should_send,
            "would_send": decision.send,
            "send_reason": decision.reason,
            "overrode_cooldown": decision.overrode_cooldown,
            "kind": alert.kind,
            "sms_en": alert.sms_for("en"),
            "sms_sw": alert.sms_for("sw"),
            "reason": risk.reasons[0] if risk.reasons else "",
            "spray_window": advice.best.label if (advice and advice.best) else "",
            "rows_seen": len(past),
        })

        day += pd.Timedelta(days=1)

    return pd.DataFrame(rows)


def changes_only(results: pd.DataFrame) -> pd.DataFrame:
    """Keep the days where the message would actually change.

    A live service would not send the same MODERATE text 40 mornings running.
    This keeps every level transition plus every HIGH day, which is what a
    farmer would really receive.
    """
    if results.empty:
        return results
    changed = results["level"] != results["level"].shift()
    return results[changed | (results["level"] == "HIGH")]


def summarise(results: pd.DataFrame, source: str) -> None:
    print()
    print(RULE)
    print("BACKTEST SUMMARY")
    print(RULE)
    print(f"  source      : {source}")

    if results.empty:
        print("  No days could be assessed.")
        return

    print(f"  period      : {results['date'].min()}  ->  {results['date'].max()}")
    print(f"  days tested : {len(results)}")

    print()
    print("  Risk level by day:")
    for level in (*config.RISK_LEVELS, de.UNKNOWN):
        n = int((results["level"] == level).sum())
        if n:
            pct = 100 * n / len(results)
            bar = "#" * int(40 * n / len(results))
            print(f"    {level:<9} {n:>4} days ({pct:>5.1f}%)  {bar}")

    n_days = len(results)
    n_eligible = int(results["eligible"].sum())
    n_send = int(results["would_send"].sum())

    print()
    print("  SMS volume:")
    print(f"    Without a send policy (every eligible day):"
          f" {n_eligible:>4} texts  ({100 * n_eligible / n_days:>5.1f}% of days)")
    print(f"    With the send policy applied:             "
          f" {n_send:>4} texts  ({100 * n_send / n_days:>5.1f}% of days)")
    if n_eligible:
        cut = 100 * (1 - n_send / n_eligible)
        print(f"    Reduction: {cut:.0f}% fewer texts")

    by_level = results[results["would_send"]].groupby("level").size()
    if len(by_level):
        print()
        print("    Texts by level:")
        for level, n in by_level.items():
            print(f"      {level:<9} {n}")
    n_override = int(results["overrode_cooldown"].sum())
    if n_override:
        print(f"    HIGH escalations that overrode the cooldown: {n_override}")

    hutton_days = results[results["consecutive_hutton_days"] > 0]
    print()
    print(f"  Days inside a Hutton run : {len(hutton_days)}")
    high = results[results["hutton_level"] == "HIGH"]
    print(f"  Days at official HIGH    : {len(high)}")
    if len(high):
        print(f"    first: {high['date'].iloc[0]}   last: {high['date'].iloc[-1]}")
        longest = int(results["consecutive_hutton_days"].max())
        print(f"    longest Hutton run: {longest} consecutive days")

    unknown = results[results["level"] == de.UNKNOWN]
    if len(unknown):
        print()
        print(f"  Days we declined to judge (data gaps): {len(unknown)} "
              f"({100 * len(unknown) / len(results):.1f}%)")


def print_timeline(results: pd.DataFrame, *, all_days: bool, limit: int = 60) -> None:
    print()
    print(RULE)
    print("ALERT TIMELINE" + ("" if all_days else "  (level changes and HIGH days only)"))
    print(RULE)

    shown = results if all_days else changes_only(results)
    if shown.empty:
        print("  Nothing to show.")
        return

    if len(shown) > limit:
        print(f"  ({len(shown)} rows, showing the first {limit})")
        shown = shown.head(limit)

    for _, r in shown.iterrows():
        flag = "SEND" if r["would_send"] else "    "
        run = f" run={int(r['consecutive_hutton_days'])}d" if r["consecutive_hutton_days"] else ""
        print(f"  {r['date']}  [{flag}]  {r['level']:<8} "
              f"humid={r['humid_hours_24h']:>4.0f}h{run}")
        if r["would_send"]:
            print(f"      EN: {r['sms_en']}")
            print(f"      SW: {r['sms_sw']}")


def main() -> int:
    ap = argparse.ArgumentParser(description="Replay history and show alerts that would have fired")
    ap.add_argument("--file", type=Path, default=None, help="History file to replay")
    ap.add_argument("--alert-hour", type=int, default=DEFAULT_ALERT_HOUR,
                    help=f"Hour of day the daily alert goes out (default {DEFAULT_ALERT_HOUR})")
    ap.add_argument("--from", dest="start", default=None, help="First date (YYYY-MM-DD)")
    ap.add_argument("--to", dest="end", default=None, help="Last date (YYYY-MM-DD)")
    ap.add_argument("--all-days", action="store_true",
                    help="Show every day, not just level changes and HIGH days")
    ap.add_argument("--no-spray", action="store_true",
                    help="Skip the perfect-foresight spray windows")
    ap.add_argument("--csv", type=Path, default=None, help="Write results to CSV")
    args = ap.parse_args()

    print(RULE)
    print("SHAMBA PULSE - BACKTEST")
    print(RULE)

    df, source = load_history(args.file)
    if df.empty:
        print(f"\nNo historical data found in {config.DATA_DIR}.")
        print("Run analysis/fetch_history.py first, or pass --file.")
        return 1

    missing = dp.check_required(df)
    if "humidity_pct" in missing or "temperature_c" in missing:
        print(f"\nHistory is missing {missing}; the blight engine needs "
              f"temperature and humidity.")
        return 1

    print(f"\nLoaded {len(df):,} readings from {source}")
    print(f"  {df['timestamp'].min()}  ->  {df['timestamp'].max()}")
    if not args.no_spray:
        print("\nNOTE: spray windows below use the weather that ACTUALLY followed,")
        print("      because no forecast archive exists. They are PERFECT-FORESIGHT")
        print("      and show the ideal advice, not what the app would have said.")
        print("      The disease alerts use past observations only.")

    results = run_backtest(
        df,
        alert_hour=args.alert_hour,
        start=args.start,
        end=args.end,
        with_spray=not args.no_spray,
    )

    summarise(results, source)
    print_timeline(results, all_days=args.all_days)

    if args.csv and not results.empty:
        args.csv.parent.mkdir(parents=True, exist_ok=True)
        results.to_csv(args.csv, index=False)
        print(f"\nWrote {len(results)} rows -> {args.csv}")

    print()
    print(RULE)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
