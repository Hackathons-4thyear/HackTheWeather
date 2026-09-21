"""Send alerts over SMS via Africa's Talking.

Three modes, chosen by configuration rather than by code changes:

  DRY RUN   (default)  - print the message, send nothing. Safe for demos.
  SANDBOX              - AT_USERNAME=sandbox, hits Africa's Talking' sandbox
                         endpoint. Messages appear in the AT simulator, no
                         real SMS, no cost.
  LIVE                 - a real AT username and key. Sends actual SMS.

SMS_DRY_RUN defaults to TRUE so a misconfigured demo can never quietly spend
the team's credit or text a real farmer. Going live takes a deliberate
SMS_DRY_RUN=false in .env.

We call the AT REST endpoint directly with `requests` rather than adding the
africastalking SDK - it is one form POST, and the dependency list stays minimal.

Like every other network client here, this never raises into the caller.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import requests

import config
from services import messages as msg
from services.alerts import Alert

# Africa's Talking has separate hosts for sandbox and live traffic.
AT_SANDBOX_URL = "https://api.sandbox.africastalking.com/version1/messaging"
AT_LIVE_URL = "https://api.africastalking.com/version1/messaging"

AT_TIMEOUT_SECONDS = 20


@dataclass
class SendResult:
    """Outcome of one send attempt. Never raises - inspect .ok and .error."""

    ok: bool
    mode: str                       # "dry_run" | "sandbox" | "live"
    recipients: list[str] = field(default_factory=list)
    message: str = ""
    error: str | None = None
    status_code: int | None = None
    raw: dict[str, Any] | None = None
    cost: str | None = None

    @property
    def n_sent(self) -> int:
        return len(self.recipients) if self.ok else 0


def current_mode() -> str:
    """Which mode a send would use right now, given the environment."""
    if config.SMS_DRY_RUN:
        return "dry_run"
    if (config.AT_USERNAME or "").strip().lower() == "sandbox":
        return "sandbox"
    return "live"


def _endpoint(mode: str) -> str:
    return AT_SANDBOX_URL if mode == "sandbox" else AT_LIVE_URL


def _normalise_recipients(to: str | list[str]) -> list[str]:
    """Accept a single number or a list; return E.164-ish strings.

    Kenyan numbers are commonly written 07XXXXXXXX locally. Africa's Talking
    wants +2547XXXXXXXX, so we convert rather than letting the send fail.
    """
    numbers = [to] if isinstance(to, str) else list(to)
    out: list[str] = []
    for raw in numbers:
        n = str(raw).strip().replace(" ", "").replace("-", "")
        if not n:
            continue
        if n.startswith("+"):
            out.append(n)
        elif n.startswith("07") or n.startswith("01"):
            out.append("+254" + n[1:])       # drop the local trunk 0
        elif n.startswith("254"):
            out.append("+" + n)
        else:
            out.append(n)                     # leave anything else untouched
    return out


def send_sms(
    to: str | list[str],
    message: str,
    *,
    sender_id: str | None = None,
    force_mode: str | None = None,
) -> SendResult:
    """Send one message to one or more numbers. Never raises."""
    mode = force_mode or current_mode()
    recipients = _normalise_recipients(to)

    if not recipients:
        return SendResult(ok=False, mode=mode, message=message,
                          error="no valid recipient numbers")

    if not message.strip():
        return SendResult(ok=False, mode=mode, recipients=recipients,
                          error="refusing to send an empty message")

    if len(message) > config.SMS_MAX_CHARS:
        # Not fatal - the carrier will split it - but the farmer pays twice,
        # so make the overspend visible instead of silent.
        print(f"[sms] WARNING: message is {len(message)} chars, over the "
              f"{config.SMS_MAX_CHARS}-char single-segment limit.")

    # ---- Dry run: print and stop. ----------------------------------------
    if mode == "dry_run":
        print("-" * 70)
        print("[sms] DRY RUN - nothing was sent")
        print(f"  to     : {', '.join(recipients)}")
        print(f"  chars  : {len(message)}/{config.SMS_MAX_CHARS}")
        print(f"  message: {message}")
        print("-" * 70)
        return SendResult(ok=True, mode=mode, recipients=recipients,
                          message=message)

    # ---- Sandbox or live: real HTTP call. --------------------------------
    if not config.AT_API_KEY:
        return SendResult(ok=False, mode=mode, recipients=recipients,
                          message=message,
                          error="AT_API_KEY missing - set it in .env")

    payload = {
        "username": config.AT_USERNAME,
        "to": ",".join(recipients),
        "message": message,
    }
    if sender_id:
        payload["from"] = sender_id

    headers = {
        "apiKey": config.AT_API_KEY,
        "Content-Type": "application/x-www-form-urlencoded",
        "Accept": "application/json",
        "User-Agent": config.USER_AGENT,
    }

    try:
        resp = requests.post(_endpoint(mode), data=payload, headers=headers,
                             timeout=AT_TIMEOUT_SECONDS)
    except requests.exceptions.Timeout:
        return SendResult(ok=False, mode=mode, recipients=recipients,
                          message=message,
                          error=f"Africa's Talking timed out after {AT_TIMEOUT_SECONDS}s")
    except requests.exceptions.RequestException as exc:
        return SendResult(ok=False, mode=mode, recipients=recipients,
                          message=message, error=f"network error: {exc}")

    if resp.status_code not in (200, 201):
        return SendResult(ok=False, mode=mode, recipients=recipients,
                          message=message, status_code=resp.status_code,
                          error=f"HTTP {resp.status_code}: {resp.text[:200]}")

    try:
        data = resp.json()
    except ValueError:
        return SendResult(ok=False, mode=mode, recipients=recipients,
                          message=message, status_code=resp.status_code,
                          error="response was not valid JSON")

    # AT wraps results as {"SMSMessageData": {"Message": ..., "Recipients": [...]}}
    smd = data.get("SMSMessageData", {}) if isinstance(data, dict) else {}
    recs = smd.get("Recipients", []) or []
    failed = [r for r in recs if str(r.get("status", "")).lower() != "success"]

    if failed:
        detail = "; ".join(f"{r.get('number')}: {r.get('status')}" for r in failed[:3])
        return SendResult(ok=False, mode=mode, recipients=recipients,
                          message=message, status_code=resp.status_code,
                          raw=data, error=f"{len(failed)} recipient(s) failed: {detail}")

    return SendResult(
        ok=True, mode=mode, recipients=recipients, message=message,
        status_code=resp.status_code, raw=data,
        cost=smd.get("Message"),
    )


def send_alert(
    alert: Alert,
    to: str | list[str],
    *,
    lang: str = "en",
    force: bool = False,
    **kwargs: Any,
) -> SendResult:
    """Send an Alert in the given language.

    Respects Alert.should_send - a LOW-risk or UNKNOWN alert is skipped unless
    `force=True`, because a daily all-clear trains farmers to ignore the service.
    """
    if lang not in msg.LANGUAGES:
        return SendResult(ok=False, mode=current_mode(),
                          error=f"unknown language {lang!r}; "
                                f"expected one of {msg.LANGUAGES}")

    if not alert.should_send and not force:
        return SendResult(
            ok=True, mode="skipped", recipients=[],
            message=alert.sms_for(lang),
            error=f"not sent: {alert.level} risk does not warrant an SMS "
                  f"(pass force=True to override)",
        )

    return send_sms(to, alert.sms_for(lang), **kwargs)


def describe_config() -> str:
    """One-line summary of how SMS is configured, for the dashboard banner."""
    mode = current_mode()
    if mode == "dry_run":
        return "SMS: DRY RUN - messages are printed, not sent."
    if mode == "sandbox":
        return ("SMS: Africa's Talking SANDBOX - messages go to the AT "
                "simulator, no real delivery, no cost.")
    key_set = "set" if config.AT_API_KEY else "MISSING"
    return f"SMS: LIVE as {config.AT_USERNAME!r} (API key {key_set}) - real messages will be sent."
