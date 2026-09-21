"""Tests for alert composition and the SMS sender.

The hard requirement: every rendered message in EVERY language must fit
SMS_MAX_CHARS, for every template and every plausible set of field values. A
message that overflows costs the farmer a second segment.

No test here touches the network. The sender is exercised against a stub.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import config  # noqa: E402
from services import alerts, messages as msg, sms_sender  # noqa: E402
from services import disease_engine as de  # noqa: E402
from services import spray_window as sw  # noqa: E402
from tests.test_disease_engine import make_days  # noqa: E402

NOW = pd.Timestamp("2025-10-08 07:00:00")


def a_window(start="2025-10-09 06:30:00", end="2025-10-09 10:00:00") -> sw.SprayWindow:
    return sw.SprayWindow(
        start=pd.Timestamp(start), end=pd.Timestamp(end), duration_hours=3.5,
        mean_wind_ms=2.4, max_wind_ms=3.1, min_wind_ms=1.6,
        max_humidity_pct=72.0, dry_hours_after=14.0, score=0.8,
        preferred_overlap_h=3, reasons=["test window"],
    )


def advice_with_window() -> sw.SprayAdvice:
    return sw.SprayAdvice(windows=[a_window()], reasons=["found one"])


def risk_high() -> de.RiskAssessment:
    return de.assess(make_days([
        {"day": "2025-10-06", "humid_hours": 9, "min_temp": 14.0},
        {"day": "2025-10-07", "humid_hours": 11, "min_temp": 13.0},
    ]))


def risk_moderate() -> de.RiskAssessment:
    return de.assess(make_days([
        {"day": "2025-10-06", "humid_hours": 2, "min_temp": 15.0},
        {"day": "2025-10-07", "humid_hours": 7, "min_temp": 14.0},
    ]))


def risk_low() -> de.RiskAssessment:
    return de.assess(make_days([
        {"day": "2025-10-06", "humid_hours": 0, "min_temp": 16.0},
        {"day": "2025-10-07", "humid_hours": 1, "min_temp": 17.0},
    ]))


def risk_unknown() -> de.RiskAssessment:
    return de.assess(make_days([
        {"day": "2025-10-07", "humid_hours": 0, "min_temp": 15.0,
         "drop_hours": list(range(8, 24))},
    ]))


# --------------------------------------------------------------------------
# The 160-character requirement
# --------------------------------------------------------------------------

@pytest.mark.parametrize("kind", sorted(msg.TEMPLATES))
@pytest.mark.parametrize("lang", msg.LANGUAGES)
@pytest.mark.parametrize("variant", ["full", "short"])
def test_every_template_fits_one_sms_segment(kind, lang, variant):
    """Rendered with realistically long values, nothing may exceed 160 chars."""
    text = msg.TEMPLATES[kind][lang][variant].format(
        brand=msg.BRAND,
        crop=msg.CROPS[lang]["both"],
        rh="90",
        hours="24",                      # widest plausible hour count
        days="14",                       # widest plausible day run
        window="Jumatano 06:30-10:00",   # a long, unabbreviated day name
        wind="2.5",
    )
    assert len(text) <= config.SMS_MAX_CHARS, (
        f"{kind}/{lang}/{variant} is {len(text)} chars: {text}"
    )


@pytest.mark.parametrize("lang", msg.LANGUAGES)
def test_built_alerts_fit_one_segment_in_every_language(lang):
    for risk in (risk_high(), risk_moderate(), risk_low(), risk_unknown()):
        for advice in (advice_with_window(), sw.SprayAdvice()):
            alert = alerts.build_alert(risk, advice, now=NOW)
            assert alert.length_for(lang) <= config.SMS_MAX_CHARS, (
                f"{alert.kind}/{lang}: {alert.length_for(lang)} chars"
            )


def test_both_languages_are_always_produced():
    alert = alerts.build_alert(risk_high(), advice_with_window(), now=NOW)
    for lang in msg.LANGUAGES:
        assert alert.sms_for(lang).strip(), f"{lang} message is empty"


def test_overlong_render_falls_back_to_short_variant(monkeypatch):
    long_full = "X" * 200 + " {brand}"
    patched = dict(msg.TEMPLATES)
    patched["blight_high"] = {
        lang: {"full": long_full, "short": "{brand}: short form"}
        for lang in msg.LANGUAGES
    }
    monkeypatch.setattr(msg, "TEMPLATES", patched)

    alert = alerts.build_alert(risk_high(), advice_with_window(), now=NOW)
    assert alert.sms_for("en") == f"{msg.BRAND}: short form"
    assert set(alert.truncated_languages) == set(msg.LANGUAGES)


def test_hard_cut_when_even_short_overflows(monkeypatch):
    patched = dict(msg.TEMPLATES)
    patched["blight_high"] = {
        lang: {"full": "Y" * 300, "short": "Z" * 300} for lang in msg.LANGUAGES
    }
    monkeypatch.setattr(msg, "TEMPLATES", patched)
    alert = alerts.build_alert(risk_high(), advice_with_window(), now=NOW)
    assert alert.length_for("en") == config.SMS_MAX_CHARS
    assert alert.sms_for("en").endswith("…")


# --------------------------------------------------------------------------
# Template selection
# --------------------------------------------------------------------------

def test_high_risk_with_window_recommends_spraying():
    alert = alerts.build_alert(risk_high(), advice_with_window(), now=NOW)
    assert alert.kind == "blight_high"
    assert alert.level == "HIGH"
    assert alert.should_send


def test_high_risk_without_window_says_so_instead_of_inventing_one():
    alert = alerts.build_alert(risk_high(), sw.SprayAdvice(), now=NOW)
    assert alert.kind == "blight_high_no_window"
    assert "No safe spray window" in alert.sms_for("en")


def test_unknown_risk_uses_the_insufficient_data_template():
    alert = alerts.build_alert(risk_unknown(), advice_with_window(), now=NOW)
    assert alert.kind == "insufficient_data"
    assert alert.level == de.UNKNOWN


def test_low_risk_with_window_offers_the_window():
    alert = alerts.build_alert(risk_low(), advice_with_window(), now=NOW)
    assert alert.kind == "spray_window"


def test_low_risk_without_window_is_an_all_clear():
    alert = alerts.build_alert(risk_low(), sw.SprayAdvice(), now=NOW)
    assert alert.kind == "blight_low"


# --------------------------------------------------------------------------
# Send policy
# --------------------------------------------------------------------------

def test_only_moderate_and_high_are_worth_an_sms():
    assert alerts.build_alert(risk_high(), advice_with_window(), now=NOW).should_send
    assert alerts.build_alert(risk_moderate(), advice_with_window(), now=NOW).should_send
    assert not alerts.build_alert(risk_low(), advice_with_window(), now=NOW).should_send
    assert not alerts.build_alert(risk_unknown(), advice_with_window(), now=NOW).should_send


# --------------------------------------------------------------------------
# Day naming
# --------------------------------------------------------------------------

def test_relative_day_naming():
    today = pd.Timestamp("2025-10-08 07:00")
    assert msg.relative_day(today, today, "en") == "today"
    assert msg.relative_day(today + pd.Timedelta(days=1), today, "en") == "tomorrow"
    assert msg.relative_day(today, today, "sw") == "leo"
    assert msg.relative_day(today + pd.Timedelta(days=1), today, "sw") == "kesho"


def test_distant_day_uses_a_weekday_name():
    today = pd.Timestamp("2025-10-08 07:00")     # Wednesday
    far = today + pd.Timedelta(days=3)           # Saturday
    assert msg.relative_day(far, today, "en") == "Sat"
    assert msg.relative_day(far, today, "sw") == "Jms"


def test_window_formatting_is_actionable():
    assert msg.format_window(pd.Timestamp("2025-10-09 06:30"),
                             pd.Timestamp("2025-10-09 10:00"),
                             NOW, "en") == "tomorrow 06:30-10:00"


# --------------------------------------------------------------------------
# Translation review flag
# --------------------------------------------------------------------------

def test_kiswahili_is_flagged_as_unreviewed_until_a_native_speaker_signs_off():
    """Guards against quietly shipping unreviewed translations to farmers."""
    assert msg.SW_TRANSLATION_REVIEWED is False
    alert = alerts.build_alert(risk_high(), advice_with_window(), now=NOW)
    assert "translation unreviewed" in alerts.preview(alert)


def test_every_template_exists_in_every_language():
    for kind, langs in msg.TEMPLATES.items():
        for lang in msg.LANGUAGES:
            assert lang in langs, f"{kind} is missing {lang}"
            assert "full" in langs[lang] and "short" in langs[lang]


# --------------------------------------------------------------------------
# SMS sender - no network touched
# --------------------------------------------------------------------------

def test_dry_run_sends_nothing(monkeypatch, capsys):
    monkeypatch.setattr(config, "SMS_DRY_RUN", True)

    def explode(*a, **k):
        raise AssertionError("dry run must not make an HTTP request")

    monkeypatch.setattr(sms_sender.requests, "post", explode)

    result = sms_sender.send_sms("0712345678", "test message")
    assert result.ok
    assert result.mode == "dry_run"
    assert "test message" in capsys.readouterr().out


def test_dry_run_is_the_default_mode(monkeypatch):
    monkeypatch.setattr(config, "SMS_DRY_RUN", True)
    assert sms_sender.current_mode() == "dry_run"
    assert "DRY RUN" in sms_sender.describe_config()


def test_sandbox_mode_uses_the_sandbox_endpoint(monkeypatch):
    monkeypatch.setattr(config, "SMS_DRY_RUN", False)
    monkeypatch.setattr(config, "AT_USERNAME", "sandbox")
    monkeypatch.setattr(config, "AT_API_KEY", "fake-key")
    assert sms_sender.current_mode() == "sandbox"

    captured = {}

    class FakeResp:
        status_code = 201
        text = ""

        def json(self):
            return {"SMSMessageData": {
                "Message": "Sent to 1/1 Total Cost: KES 0.8000",
                "Recipients": [{"number": "+254712345678", "status": "Success"}],
            }}

    def fake_post(url, data=None, headers=None, timeout=None):
        captured["url"] = url
        captured["data"] = data
        captured["headers"] = headers
        return FakeResp()

    monkeypatch.setattr(sms_sender.requests, "post", fake_post)

    result = sms_sender.send_sms("0712345678", "hello")
    assert result.ok
    assert captured["url"] == sms_sender.AT_SANDBOX_URL
    assert captured["headers"]["apiKey"] == "fake-key"
    assert result.cost.startswith("Sent to 1/1")


def test_live_mode_uses_the_live_endpoint(monkeypatch):
    monkeypatch.setattr(config, "SMS_DRY_RUN", False)
    monkeypatch.setattr(config, "AT_USERNAME", "realuser")
    monkeypatch.setattr(config, "AT_API_KEY", "fake-key")
    assert sms_sender.current_mode() == "live"

    seen = {}

    class FakeResp:
        status_code = 201
        text = ""

        def json(self):
            return {"SMSMessageData": {"Recipients": [{"status": "Success"}]}}

    monkeypatch.setattr(sms_sender.requests, "post",
                        lambda url, **kw: (seen.update(url=url), FakeResp())[1])
    sms_sender.send_sms("0712345678", "hello")
    assert seen["url"] == sms_sender.AT_LIVE_URL


def test_kenyan_local_numbers_are_converted_to_e164():
    assert sms_sender._normalise_recipients("0712345678") == ["+254712345678"]
    assert sms_sender._normalise_recipients("0112345678") == ["+254112345678"]
    assert sms_sender._normalise_recipients("254712345678") == ["+254712345678"]
    assert sms_sender._normalise_recipients("+254712345678") == ["+254712345678"]
    assert sms_sender._normalise_recipients("071 234 5678") == ["+254712345678"]
    assert sms_sender._normalise_recipients(["0712345678", "0723456789"]) == \
        ["+254712345678", "+254723456789"]


def test_empty_message_is_refused(monkeypatch):
    monkeypatch.setattr(config, "SMS_DRY_RUN", True)
    result = sms_sender.send_sms("0712345678", "   ")
    assert not result.ok
    assert "empty" in result.error


def test_no_recipients_is_refused(monkeypatch):
    monkeypatch.setattr(config, "SMS_DRY_RUN", True)
    result = sms_sender.send_sms([], "hello")
    assert not result.ok
    assert "recipient" in result.error


def test_missing_api_key_is_reported_not_crashed(monkeypatch):
    monkeypatch.setattr(config, "SMS_DRY_RUN", False)
    monkeypatch.setattr(config, "AT_USERNAME", "sandbox")
    monkeypatch.setattr(config, "AT_API_KEY", "")
    result = sms_sender.send_sms("0712345678", "hello")
    assert not result.ok
    assert "AT_API_KEY" in result.error


def test_network_failure_is_reported_not_raised(monkeypatch):
    monkeypatch.setattr(config, "SMS_DRY_RUN", False)
    monkeypatch.setattr(config, "AT_USERNAME", "sandbox")
    monkeypatch.setattr(config, "AT_API_KEY", "k")

    def boom(*a, **k):
        raise sms_sender.requests.exceptions.ConnectionError("no route to host")

    monkeypatch.setattr(sms_sender.requests, "post", boom)
    result = sms_sender.send_sms("0712345678", "hello")
    assert not result.ok
    assert "network error" in result.error


def test_failed_recipient_is_surfaced(monkeypatch):
    monkeypatch.setattr(config, "SMS_DRY_RUN", False)
    monkeypatch.setattr(config, "AT_USERNAME", "sandbox")
    monkeypatch.setattr(config, "AT_API_KEY", "k")

    class FakeResp:
        status_code = 201
        text = ""

        def json(self):
            return {"SMSMessageData": {"Recipients": [
                {"number": "+254712345678", "status": "InsufficientBalance"}]}}

    monkeypatch.setattr(sms_sender.requests, "post", lambda *a, **k: FakeResp())
    result = sms_sender.send_sms("0712345678", "hello")
    assert not result.ok
    assert "InsufficientBalance" in result.error


def test_send_alert_skips_low_risk(monkeypatch):
    monkeypatch.setattr(config, "SMS_DRY_RUN", True)
    alert = alerts.build_alert(risk_low(), advice_with_window(), now=NOW)
    result = sms_sender.send_alert(alert, "0712345678")
    assert result.mode == "skipped"
    assert result.n_sent == 0


def test_send_alert_force_overrides_the_skip(monkeypatch):
    monkeypatch.setattr(config, "SMS_DRY_RUN", True)
    alert = alerts.build_alert(risk_low(), advice_with_window(), now=NOW)
    result = sms_sender.send_alert(alert, "0712345678", force=True)
    assert result.ok and result.mode == "dry_run"


def test_send_alert_rejects_an_unknown_language(monkeypatch):
    monkeypatch.setattr(config, "SMS_DRY_RUN", True)
    alert = alerts.build_alert(risk_high(), advice_with_window(), now=NOW)
    result = sms_sender.send_alert(alert, "0712345678", lang="fr")
    assert not result.ok
    assert "unknown language" in result.error


@pytest.mark.parametrize("lang", msg.LANGUAGES)
def test_send_alert_can_send_either_language(monkeypatch, capsys, lang):
    monkeypatch.setattr(config, "SMS_DRY_RUN", True)
    alert = alerts.build_alert(risk_high(), advice_with_window(), now=NOW)
    result = sms_sender.send_alert(alert, "0712345678", lang=lang)
    assert result.ok
    assert alert.sms_for(lang) in capsys.readouterr().out
