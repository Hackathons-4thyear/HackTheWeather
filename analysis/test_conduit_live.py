"""Probe the live Conduit API and print exactly what comes back.

Run this once with a real key to CONFIRM the API contract. It prints the raw
HTTP response and, if it parses, the shape of the records - so we can pin down
the real column names instead of guessing.

    .venv/Scripts/python.exe analysis/test_conduit_live.py
    .venv/Scripts/python.exe analysis/test_conduit_live.py 2026-09-14 2026-09-21
"""

from __future__ import annotations

import json
import sys
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import requests  # noqa: E402

import config  # noqa: E402
from services import conduit_api  # noqa: E402


def main() -> int:
    if len(sys.argv) >= 3:
        fromdate, todate = sys.argv[1], sys.argv[2]
    else:
        today = date.today()
        fromdate = (today - timedelta(days=2)).isoformat()
        todate = today.isoformat()

    print("=" * 70)
    print("CONDUIT LIVE API PROBE")
    print("=" * 70)
    print(f"URL       : {config.CONDUIT_URL}")
    print(f"email     : {config.CONDUIT_EMAIL or '(MISSING - set CONDUIT_EMAIL in .env)'}")
    key = config.CONDUIT_API_KEY
    print(f"apikey    : {(key[:4] + '...' + key[-4:]) if len(key) > 8 else '(MISSING - set CONDUIT_API_KEY in .env)'}")
    print(f"fromdate  : {fromdate}")
    print(f"todate    : {todate}")
    print()

    if not key or not config.CONDUIT_EMAIL:
        print("Cannot call the API without credentials.")
        print("Copy .env.example to .env and fill in CONDUIT_API_KEY and CONDUIT_EMAIL.")
        return 1

    # --- 1. Raw request, so we see the response exactly as the server sends it.
    form = {
        "apikey": key,
        "email": config.CONDUIT_EMAIL,
        "fromdate": fromdate,
        "todate": todate,
    }
    headers = {
        "User-Agent": config.USER_AGENT,
        "Accept": "application/json, text/plain, */*",
    }
    try:
        resp = requests.post(
            config.CONDUIT_URL,
            data=form,
            headers=headers,
            timeout=config.CONDUIT_TIMEOUT_SECONDS,
        )
    except requests.exceptions.RequestException as exc:
        print(f"REQUEST FAILED: {type(exc).__name__}: {exc}")
        return 2

    print("-" * 70)
    print(f"HTTP {resp.status_code}  ({len(resp.content)} bytes)")
    print(f"Content-Type: {resp.headers.get('Content-Type')}")
    print("-" * 70)
    print("RAW BODY (first 3000 chars):")
    print(resp.text[:3000])
    if len(resp.text) > 3000:
        print(f"... [{len(resp.text) - 3000} more chars]")
    print()

    # --- 2. Parsed through our defensive client.
    print("-" * 70)
    print("PARSED VIA services/conduit_api.py")
    print("-" * 70)
    result = conduit_api.fetch_range(fromdate, todate)
    print(f"ok         : {result.ok}")
    print(f"error      : {result.error}")
    print(f"n_rows     : {result.n_rows}")

    if result.rows:
        first = result.rows[0]
        print(f"\nColumns in first record ({len(first)}):")
        for k, v in first.items():
            print(f"  {k:<24} = {v!r}   ({type(v).__name__})")

        print("\nFirst record as JSON:")
        print(json.dumps(first, indent=2, default=str)[:1500])

        if len(result.rows) > 1:
            print("\nLast record as JSON:")
            print(json.dumps(result.rows[-1], indent=2, default=str)[:1500])

        # Save the raw rows so we can work offline afterwards.
        out = config.DATA_DIR / f"conduit_probe_{fromdate}_to_{todate}.json"
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(result.rows, indent=2, default=str), encoding="utf-8")
        print(f"\nSaved {result.n_rows} rows -> {out}")

    return 0 if result.ok else 3


if __name__ == "__main__":
    raise SystemExit(main())
