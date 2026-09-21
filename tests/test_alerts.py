"""Tests for alert composition and the SMS sender.

The hard requirement: every rendered message in EVERY language must fit
SMS_MAX_CHARS, for every template and every plausible set of field values. A
message that overflows costs the farmer a second segment.

No test here touches the network. The sender is exercised against a stub.
"""

from __future__ import annotations

import functools
import sys
from pathlib import Path

import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import config  # noqa: E402
from services import alerts, messages as msg, sms_sender  # noqa: E402
from services import disease_engine as de  # noqa: E402
from services import spray_window as sw  # noqa: E402
from services import spray_window as sw_mod  # noqa: E402
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

def reachable_minutes() -> tuple[int, ...]:
    """Minute values a spray window can actually start or end on.

    Windows sit on whole hours, except where daylight clipping trims them to
    SPRAY_DAYLIGHT_START/END. So the minute is 0 or one of those two - derived
    from config rather than hardcoded, so that moving the daylight bounds to an
    awkward time re-tests the templates automatically.
    """
    return tuple(sorted({0,
                         config.SPRAY_DAYLIGHT_START.minute,
                         config.SPRAY_DAYLIGHT_END.minute}))


@functools.lru_cache(maxsize=None)
def longest_window_for(lang: str) -> str:
    """The longest string format_window can actually produce in this language.

    Computed rather than hardcoded, because Kiswahili time is far longer than
    a 24-hour clock ("Jumamosi saa 12 na nusu asubuhi-..." vs "Sat 06:30-10:00")
    and hardcoding one language's worst case would silently under-test the other.
    """
    now = pd.Timestamp("2025-10-01 06:00")
    base = pd.Timestamp("2025-10-01")
    minutes = reachable_minutes()
    worst = ""
    for day_offset in (0, 1, 3):          # today / tomorrow / weekday name
        for h1 in range(24):
            for m1 in minutes:
                for h2 in range(24):
                    for m2 in minutes:
                        start = base + pd.Timedelta(days=day_offset, hours=h1, minutes=m1)
                        end = base + pd.Timedelta(days=day_offset, hours=h2, minutes=m2)
                        text = msg.format_window(start, end, now, lang)
                        if len(text) > len(worst):
                            worst = text
    return worst


def test_spray_windows_only_land_on_reachable_minutes():
    """Underpins the worst-case budget above.

    If a window could end on an arbitrary minute, "saa 3 na dakika 47 asubuhi"
    would blow the SMS budget. Windows are built on whole hours and clipped to
    the daylight bounds, so only those minutes occur.
    """
    from tests.test_spray_window import make_forecast
    advice = sw_mod.find_windows(make_forecast(hours=72),
                                 now=pd.Timestamp("2025-10-01 00:00"))
    assert advice.windows
    allowed = set(reachable_minutes())
    for w in advice.windows:
        assert w.start.minute in allowed, f"unexpected start minute {w.start}"
        assert w.end.minute in allowed, f"unexpected end minute {w.end}"


@pytest.mark.parametrize("kind", sorted(msg.TEMPLATES))
@pytest.mark.parametrize("lang", msg.LANGUAGES)
@pytest.mark.parametrize("variant", ["full", "short"])
def test_every_template_fits_one_sms_segment(kind, lang, variant):
    """Rendered with worst-case values, nothing may exceed 160 chars."""
    text = msg.TEMPLATES[kind][lang][variant].format(
        brand=msg.BRAND,
        crop=msg.CROPS[lang]["both"],
        rh="90",
        hours="24",                       # widest plausible hour count
        days="14",                        # widest plausible day run
        window=longest_window_for(lang),  # this language's true worst case
        wind="2.5",
    )
    assert len(text) <= config.SMS_MAX_CHARS, (
        f"{kind}/{lang}/{variant} is {len(text)} chars: {text}"
    )


def test_kiswahili_windows_are_much_longer_than_english_ones():
    """Guards the assumption behind the template budget.

    If Kiswahili time ever stopped being the longer form, the SW templates
    would have slack they do not need - and more importantly, if EN ever grew
    longer, its templates would need re-checking against a new worst case.
    """
    assert len(longest_window_for("sw")) > len(longest_window_for("en"))


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
    assert msg.relative_day(far, today, "sw") == "Jumamosi"


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


# --------------------------------------------------------------------------
# Kiswahili time - counted from dawn, not from midnight
# --------------------------------------------------------------------------

def test_swahili_time_anchors():
    """The two anchors that define the system: 06:00 is saa 12, 07:00 is saa 1."""
    assert msg.to_swahili_time(6, 0) == "saa 12 asubuhi"
    assert msg.to_swahili_time(7, 0) == "saa 1 asubuhi"


def test_swahili_time_worked_examples():
    assert msg.to_swahili_time(9, 0) == "saa 3 asubuhi"
    assert msg.to_swahili_time(12, 0) == "saa 6 mchana"
    assert msg.to_swahili_time(15, 0) == "saa 9 mchana"
    assert msg.to_swahili_time(17, 0) == "saa 11 jioni"
    assert msg.to_swahili_time(18, 0) == "saa 12 jioni"
    assert msg.to_swahili_time(19, 0) == "saa 1 usiku"
    assert msg.to_swahili_time(0, 0) == "saa 6 usiku"


@pytest.mark.parametrize("hour", range(24))
def test_swahili_hour_number_is_always_one_to_twelve(hour):
    """Never 'saa 0' and never 'saa 13'."""
    text = msg.to_swahili_time(hour, 0)
    number = int(text.split()[1])
    assert 1 <= number <= 12, text


@pytest.mark.parametrize("hour", range(24))
def test_every_hour_gets_a_period_word(hour):
    text = msg.to_swahili_time(hour, 0)
    assert any(p in text for p in ("asubuhi", "mchana", "jioni", "usiku")), text


def test_swahili_clock_advances_by_one_each_hour():
    """Consecutive hours must differ by exactly one, wrapping 12 -> 1."""
    numbers = [int(msg.to_swahili_time(h, 0).split()[1]) for h in range(24)]
    for a, b in zip(numbers, numbers[1:]):
        assert b == (a % 12) + 1, f"{a} -> {b} is not consecutive"


@pytest.mark.parametrize("hour", range(24))
def test_half_hours(hour):
    """Half past inserts 'na nusu' between the hour number and the period."""
    number = int(msg.to_swahili_time(hour, 0).split()[1])
    period = msg.swahili_period(hour)
    assert msg.to_swahili_time(hour, 30) == f"saa {number} na nusu {period}"


def test_half_past_reads_naturally():
    assert msg.to_swahili_time(6, 30) == "saa 12 na nusu asubuhi"
    assert msg.to_swahili_time(18, 30) == "saa 12 na nusu jioni"


def test_other_minutes_are_spelled_out():
    assert msg.to_swahili_time(9, 15) == "saa 3 na dakika 15 asubuhi"


def test_period_boundaries():
    assert msg.swahili_period(5) == "usiku"
    assert msg.swahili_period(6) == "asubuhi"
    assert msg.swahili_period(11) == "asubuhi"
    assert msg.swahili_period(12) == "mchana"
    assert msg.swahili_period(15) == "mchana"
    assert msg.swahili_period(16) == "jioni"
    assert msg.swahili_period(18) == "jioni"
    assert msg.swahili_period(19) == "usiku"


def test_invalid_times_are_rejected():
    with pytest.raises(ValueError):
        msg.to_swahili_time(24, 0)
    with pytest.raises(ValueError):
        msg.to_swahili_time(-1, 0)
    with pytest.raises(ValueError):
        msg.to_swahili_time(9, 60)


def test_swahili_window_uses_swahili_time_not_clock_time():
    """The example from review: 09:00-17:00 must not appear as digits."""
    text = msg.format_window(pd.Timestamp("2025-10-09 09:00"),
                             pd.Timestamp("2025-10-09 17:00"),
                             pd.Timestamp("2025-10-08 07:00"), "sw")
    assert text == "kesho saa 3 asubuhi-saa 11 jioni"
    assert "09:00" not in text and "17:00" not in text


def test_english_window_still_uses_clock_time():
    text = msg.format_window(pd.Timestamp("2025-10-09 09:00"),
                             pd.Timestamp("2025-10-09 17:00"),
                             pd.Timestamp("2025-10-08 07:00"), "en")
    assert text == "tomorrow 09:00-17:00"


# --------------------------------------------------------------------------
# Kiswahili wording
# --------------------------------------------------------------------------

def test_blight_is_named_fully_as_baka_chelewa():
    """'baka' alone is too vague - it can mean any spot or blemish."""
    for kind in ("blight_high", "blight_moderate", "blight_low"):
        for variant in ("full", "short"):
            text = msg.TEMPLATES[kind]["sw"][variant]
            assert "baka chelewa" in text.lower(), f"{kind}/{variant}: {text}"


def test_no_bare_baka_without_chelewa():
    for kind, langs in msg.TEMPLATES.items():
        for variant in ("full", "short"):
            text = langs["sw"][variant].lower()
            for part in text.split("baka")[1:]:
                assert part.lstrip().startswith("chelewa"), f"{kind}/{variant}: {text}"


def test_durations_use_masaa_so_they_cannot_be_read_as_clock_times():
    """'saa 11' means 17:00. A duration of 11 hours must read 'masaa 11'."""
    for kind, langs in msg.TEMPLATES.items():
        text = langs["sw"]["full"]
        if "{hours}" in text:
            assert "masaa {hours}" in text, f"{kind} uses a clock-ambiguous form: {text}"


def test_weekdays_are_full_words_not_abbreviations():
    for name in msg._WEEKDAYS["sw"]:
        assert len(name) > 3, f"{name} is an abbreviation"
        assert name.startswith(("Juma", "Alh", "Ijum")), name
