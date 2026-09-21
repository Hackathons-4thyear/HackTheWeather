"""Client for the JKUAT Conduit weather-station API.

The API contract was reported to us but NOT confirmed:

    POST https://conduit.jhubafrica.com/data.php
    form-encoded: apikey, email, fromdate (YYYY-MM-DD), todate (YYYY-MM-DD)
    -> JSON for that date range

Because the response shape is unknown, everything here parses defensively:
we accept a bare list, or a dict wrapping the rows under any of several
plausible keys, and we never let a network or parse failure raise into the UI.
Callers get a ConduitResult telling them whether it worked and why not.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Any

import requests

import config


# Keys a JSON envelope might hide the actual rows under. Checked in order.
_CANDIDATE_ROW_KEYS = ("data", "records", "results", "rows", "readings", "payload", "items")


@dataclass
class ConduitResult:
    """Outcome of a Conduit fetch. Never raises - inspect .ok and .error."""

    ok: bool
    rows: list[dict[str, Any]] = field(default_factory=list)
    error: str | None = None
    status_code: int | None = None
    # Raw text kept (truncated) so we can eyeball an unexpected response shape.
    raw_preview: str = ""

    @property
    def n_rows(self) -> int:
        return len(self.rows)


def _extract_rows(payload: Any) -> tuple[list[dict[str, Any]], str | None]:
    """Pull a list of record dicts out of an unknown JSON structure.

    Returns (rows, error). Handles:
      * a bare list of dicts
      * {"data": [...]} and friends
      * a single dict that is itself one record
      * a dict of dicts keyed by id/timestamp
    """
    if payload is None:
        return [], "response body was empty or not JSON"

    if isinstance(payload, list):
        rows = [r for r in payload if isinstance(r, dict)]
        if not rows and payload:
            return [], f"response was a list of {type(payload[0]).__name__}, not records"
        return rows, None

    if isinstance(payload, dict):
        # An explicit error envelope is worth surfacing verbatim.
        for err_key in ("error", "message", "status"):
            val = payload.get(err_key)
            if isinstance(val, str) and val.strip().lower() in {"error", "failed", "invalid"}:
                return [], f"API reported {err_key}={val!r}"

        for key in _CANDIDATE_ROW_KEYS:
            if key in payload:
                # An EMPTY list is a valid answer meaning "no readings in this
                # range" - the station returns {"status":"success","data":[]}
                # for a date it has not logged yet. Returning it as an empty
                # result rather than falling through to the unknown-shape
                # branch keeps the error message honest.
                if isinstance(payload[key], list):
                    return [r for r in payload[key] if isinstance(r, dict)], None
                inner, err = _extract_rows(payload[key])
                if inner:
                    return inner, None
                if err:
                    return [], f"key {key!r}: {err}"

        # dict-of-dicts keyed by something -> take the values
        values = list(payload.values())
        if values and all(isinstance(v, dict) for v in values):
            return values, None

        # A flat dict of scalars is plausibly a single reading.
        if payload and all(not isinstance(v, (dict, list)) for v in payload.values()):
            return [payload], None

        return [], f"unrecognised JSON object with keys {sorted(payload)[:12]}"

    return [], f"unexpected JSON type {type(payload).__name__}"


def fetch_range(
    fromdate: str | date,
    todate: str | date,
    *,
    apikey: str | None = None,
    email: str | None = None,
    timeout: int | None = None,
) -> ConduitResult:
    """Fetch readings between two dates (inclusive). Never raises."""
    apikey = apikey if apikey is not None else config.CONDUIT_API_KEY
    email = email if email is not None else config.CONDUIT_EMAIL
    timeout = timeout or config.CONDUIT_TIMEOUT_SECONDS

    if not apikey or not email:
        return ConduitResult(
            ok=False,
            error="CONDUIT_API_KEY / CONDUIT_EMAIL missing - set them in .env",
        )

    if isinstance(fromdate, date):
        fromdate = fromdate.isoformat()
    if isinstance(todate, date):
        todate = todate.isoformat()

    form = {"apikey": apikey, "email": email, "fromdate": fromdate, "todate": todate}

    # Form-encoded POST, exactly like the organizers' PHP http_build_query
    # example: requests serialises `data=` as application/x-www-form-urlencoded.
    # Do NOT switch this to json= - the endpoint expects form fields.
    headers = {
        "User-Agent": config.USER_AGENT,
        "Accept": "application/json, text/plain, */*",
    }

    try:
        resp = requests.post(
            config.CONDUIT_URL, data=form, headers=headers, timeout=timeout
        )
    except requests.exceptions.Timeout:
        return ConduitResult(ok=False, error=f"request timed out after {timeout}s")
    except requests.exceptions.RequestException as exc:
        return ConduitResult(ok=False, error=f"network error: {exc}")

    preview = resp.text[:2000] if resp.text else ""

    if resp.status_code != 200:
        return ConduitResult(
            ok=False,
            error=f"HTTP {resp.status_code}",
            status_code=resp.status_code,
            raw_preview=preview,
        )

    try:
        payload = resp.json()
    except (ValueError, json.JSONDecodeError):
        return ConduitResult(
            ok=False,
            error="response was not valid JSON",
            status_code=resp.status_code,
            raw_preview=preview,
        )

    rows, err = _extract_rows(payload)
    if err:
        return ConduitResult(
            ok=False, error=err, status_code=resp.status_code, raw_preview=preview
        )

    return ConduitResult(ok=True, rows=rows, status_code=resp.status_code, raw_preview=preview)


def fetch_recent(days: int = 7, **kwargs: Any) -> ConduitResult:
    """The last `days` days, including whatever exists for the current day.

    `todate` is EXCLUSIVE on this endpoint - verified 2026-09-21: requesting
    fromdate=2026-09-20&todate=2026-09-21 returns only the 2026-09-20 UTC day,
    ending 23:52 UTC. Asking through TOMORROW is therefore what includes today.

    Note the station publishes on a lag: at 10:41 UTC the current UTC day was
    still empty, so the freshest reading available is normally the end of the
    previous UTC day. That is why the dashboard reports data age explicitly
    rather than calling any successful fetch "live and current".
    """
    today = date.today()
    return fetch_range(
        today - timedelta(days=days - 1),
        today + timedelta(days=1),   # exclusive bound - include today
        **kwargs,
    )
