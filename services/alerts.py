"""Turn engine output into farmer-ready alerts.

Takes a RiskAssessment (disease_engine) and SprayAdvice (spray_window) and
produces an Alert carrying:
  * a short SMS body in English AND Kiswahili, each under SMS_MAX_CHARS, and
  * the full plain-language reasoning for the dashboard, which has no such limit.

The SMS is the constrained artefact - 160 characters is one GSM-7 segment, and
a farmer pays per segment. So templates come in full and short variants and we
degrade deliberately rather than letting the network silently split a message.

Wording lives in messages.py so a native Kiswahili speaker can review it
without touching this logic.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import pandas as pd

import config
from services import messages as msg
from services.disease_engine import RiskAssessment, _severity
from services.spray_window import SprayAdvice, SprayWindow


@dataclass
class Alert:
    """One alert, ready to send or display."""

    kind: str                       # which template was used
    level: str                      # LOW / MODERATE / HIGH / UNKNOWN
    sms: dict[str, str]             # language code -> SMS body
    reasons: list[str] = field(default_factory=list)
    window: SprayWindow | None = None
    created_at: pd.Timestamp | None = None
    truncated_languages: list[str] = field(default_factory=list)

    @property
    def should_send(self) -> bool:
        """Only MODERATE and HIGH are worth an SMS.

        A LOW-risk message every day trains farmers to ignore the service, and
        it costs them nothing to not receive it. The dashboard still shows LOW.
        """
        return self.level in ("MODERATE", "HIGH")

    def sms_for(self, lang: str) -> str:
        return self.sms.get(lang, self.sms.get("en", ""))

    def length_for(self, lang: str) -> int:
        return len(self.sms_for(lang))


def _render(kind: str, lang: str, **fields) -> tuple[str, bool]:
    """Render a template, falling back to the short variant if too long.

    Returns (text, was_truncated). If even the short form overflows, we hard-cut
    with an ellipsis rather than let the carrier split it into two billed parts.
    """
    variants = msg.TEMPLATES[kind][lang]

    full = variants["full"].format(**fields)
    if len(full) <= config.SMS_MAX_CHARS:
        return full, False

    short = variants["short"].format(**fields)
    if len(short) <= config.SMS_MAX_CHARS:
        return short, True

    return short[: config.SMS_MAX_CHARS - 1] + "…", True


def build_alert(
    risk: RiskAssessment,
    advice: SprayAdvice | None = None,
    *,
    crop: str = "both",
    now: pd.Timestamp | None = None,
) -> Alert:
    """Compose the alert for the current risk and spray situation."""
    now = pd.Timestamp.now() if now is None else now
    window = advice.best if advice is not None else None

    # Choose the template.
    if risk.level == "UNKNOWN":
        kind = "insufficient_data"
    elif risk.level == "HIGH":
        kind = "blight_high" if window else "blight_high_no_window"
    elif risk.level == "MODERATE":
        kind = "blight_moderate" if window else "blight_moderate_no_window"
    else:
        # LOW risk: if there is a good window, that is the useful message;
        # otherwise just report the all-clear.
        kind = "spray_window" if window else "blight_low"

    sms: dict[str, str] = {}
    truncated: list[str] = []

    for lang in msg.LANGUAGES:
        fields = {
            "brand": msg.BRAND,
            "crop": msg.CROPS[lang].get(crop, msg.CROPS[lang]["both"]),
            "rh": f"{config.HUTTON_RH_THRESHOLD_PCT:.0f}",
            "hours": f"{risk.humid_hours_last_24h:.0f}",
            "days": f"{max(risk.consecutive_hutton_days, 1)}",
            "window": (msg.format_window(window.start, window.end, now, lang)
                       if window else ""),
            "wind": f"{window.mean_wind_ms:.1f}" if window else "",
        }
        text, was_cut = _render(kind, lang, **fields)
        sms[lang] = text
        if was_cut:
            truncated.append(lang)

    # Dashboard reasoning: risk first, then the spray recommendation.
    reasons = list(risk.reasons)
    if advice is not None:
        if window:
            reasons.append(f"Best spray window: {window.label} ({window.quality}).")
            reasons.extend(window.reasons)
        else:
            reasons.extend(advice.reasons)

    return Alert(
        kind=kind,
        level=risk.level,
        sms=sms,
        reasons=reasons,
        window=window,
        created_at=now,
        truncated_languages=truncated,
    )


def preview(alert: Alert) -> str:
    """Human-readable dump of an alert, for the dashboard and the backtest."""
    lines = [
        f"[{alert.level}] {alert.kind}"
        + (f"  (created {alert.created_at:%Y-%m-%d %H:%M})" if alert.created_at else ""),
        f"send over SMS: {'yes' if alert.should_send else f'no ({alert.level} risk)'}",
        "",
    ]
    for lang in msg.LANGUAGES:
        text = alert.sms_for(lang)
        flag = "  [TRUNCATED]" if lang in alert.truncated_languages else ""
        note = ""
        if lang == "sw" and not msg.SW_TRANSLATION_REVIEWED:
            note = "  [translation unreviewed]"
        lines.append(f"{msg.LANGUAGE_NAMES[lang]} ({len(text)}/{config.SMS_MAX_CHARS}){flag}{note}")
        lines.append(f"  {text}")
        lines.append("")
    if alert.reasons:
        lines.append("Why:")
        lines.extend(f"  - {r}" for r in alert.reasons)
    return "\n".join(lines)


# --------------------------------------------------------------------------
# Send policy
# --------------------------------------------------------------------------
# Alert.should_send answers "is this level worth a text at all?". The policy
# below answers the harder question: "worth a text TODAY, given what we already
# sent?" It is stateful across days, so it lives in its own object rather than
# on the Alert.
#
# The dashboard ignores all of this and shows every day's level. This only
# governs SMS.

@dataclass
class SendDecision:
    """Whether to text, and the reason - so the backtest can explain itself."""

    send: bool
    reason: str
    overrode_cooldown: bool = False


@dataclass
class PolicyState:
    """What the policy remembers between days."""

    previous_level: str | None = None
    last_sent_at: pd.Timestamp | None = None
    last_sent_level: str | None = None


class AlertPolicy:
    """Decides which alerts actually get texted.

    Rules, all driven by config:
      * LOW / UNKNOWN            -> never text. No action to take.
      * HIGH                     -> always eligible; an escalation INTO HIGH
                                    ignores the cooldown entirely.
      * MODERATE                 -> only on escalation from LOW/UNKNOWN, never
                                    while it persists.
      * Same level twice         -> must be SMS_COOLDOWN_DAYS apart.
    """

    def __init__(self, state: PolicyState | None = None) -> None:
        self.state = state or PolicyState()

    # -- helpers ---------------------------------------------------------
    def _is_escalation(self, level: str) -> bool:
        """Has risk RISEN into this level since the last assessment?"""
        prev = self.state.previous_level
        if prev is None:
            return True  # first ever assessment at an actionable level
        return _severity(level) > _severity(prev)

    def _cooldown_blocks(self, level: str, now: pd.Timestamp) -> bool:
        if self.state.last_sent_at is None:
            return False
        if self.state.last_sent_level != level:
            return False  # cooldown is per level
        elapsed = (now.normalize() - self.state.last_sent_at.normalize()).days
        return elapsed < config.SMS_COOLDOWN_DAYS

    # -- the decision ----------------------------------------------------
    def decide(self, alert: Alert, now: pd.Timestamp | None = None) -> SendDecision:
        now = alert.created_at if now is None else now
        now = pd.Timestamp.now() if now is None else now
        level = alert.level

        eligible = (level in config.SMS_ALWAYS_SEND_LEVELS
                    or level in config.SMS_ESCALATION_ONLY_LEVELS)
        if not eligible:
            return SendDecision(False, f"{level}: no action needed, nothing to text")

        escalation = self._is_escalation(level)

        if level in config.SMS_ESCALATION_ONLY_LEVELS and not escalation:
            return SendDecision(
                False,
                f"{level} has not risen since yesterday - already told them")

        # An escalation into the override level beats any timer.
        if escalation and level == config.SMS_COOLDOWN_OVERRIDE_LEVEL:
            return SendDecision(
                True, f"escalated to {level} - overrides cooldown",
                overrode_cooldown=self._cooldown_blocks(level, now))

        if self._cooldown_blocks(level, now):
            days = (now.normalize() - self.state.last_sent_at.normalize()).days
            return SendDecision(
                False,
                f"cooldown: last {level} text was {days}d ago, "
                f"minimum is {config.SMS_COOLDOWN_DAYS}d")

        if escalation:
            return SendDecision(True, f"escalated to {level}")
        return SendDecision(True, f"{level} continuing, cooldown expired")

    def record(self, alert: Alert, decision: SendDecision,
               now: pd.Timestamp | None = None) -> None:
        """Update state after acting on a decision. Call once per assessment."""
        now = alert.created_at if now is None else now
        now = pd.Timestamp.now() if now is None else now
        if decision.send:
            self.state.last_sent_at = now
            self.state.last_sent_level = alert.level
        self.state.previous_level = alert.level

    def evaluate(self, alert: Alert,
                 now: pd.Timestamp | None = None) -> SendDecision:
        """decide() then record(), the usual one-call-per-day path."""
        decision = self.decide(alert, now)
        self.record(alert, decision, now)
        return decision
