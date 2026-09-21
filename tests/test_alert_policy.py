"""Tests for the SMS send policy.

The policy decides what gets TEXTED. The dashboard always shows every day's
level regardless, so nothing here may hide information from the farmer - it only
decides when spending their attention is justified.

The rule that must never bend: an escalation to HIGH always goes out. Two
consecutive Hutton days is the moment the service exists for.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import config  # noqa: E402
from services import alerts  # noqa: E402
from services import disease_engine as de  # noqa: E402

DAY = pd.Timedelta(days=1)
START = pd.Timestamp("2025-10-01 06:00")


def alert_at(level: str, when: pd.Timestamp) -> alerts.Alert:
    """A minimal Alert carrying just the fields the policy reads."""
    return alerts.Alert(
        kind="test", level=level,
        sms={"en": "x", "sw": "y"},
        created_at=when,
    )


def run_sequence(levels: list[str], *, start: pd.Timestamp = START
                 ) -> list[alerts.SendDecision]:
    """Feed one level per day and collect the decisions."""
    policy = alerts.AlertPolicy()
    out = []
    for i, level in enumerate(levels):
        when = start + i * DAY
        out.append(policy.evaluate(alert_at(level, when), when))
    return out


def sent_days(levels: list[str]) -> list[int]:
    return [i for i, d in enumerate(run_sequence(levels)) if d.send]


# --------------------------------------------------------------------------
# Levels that never text
# --------------------------------------------------------------------------

def test_low_never_texts():
    assert sent_days(["LOW"] * 10) == []


def test_unknown_never_texts():
    assert sent_days([de.UNKNOWN] * 10) == []


def test_dropping_to_low_does_not_text():
    """Good news is not worth a farmer's airtime."""
    decisions = run_sequence(["HIGH", "LOW", "LOW"])
    assert decisions[0].send
    assert not decisions[1].send
    assert not decisions[2].send


# --------------------------------------------------------------------------
# HIGH
# --------------------------------------------------------------------------

def test_escalation_to_high_always_texts():
    assert run_sequence(["LOW", "HIGH"])[1].send


def test_escalation_to_high_from_moderate_texts():
    assert run_sequence(["MODERATE", "HIGH"])[1].send


def test_escalation_to_high_from_unknown_texts():
    assert run_sequence([de.UNKNOWN, "HIGH"])[1].send


def test_escalation_to_high_overrides_the_cooldown():
    """The rule that must never bend.

    Text at HIGH, drop to MODERATE, come back to HIGH inside the cooldown -
    that second HIGH must still go out.
    """
    decisions = run_sequence(["HIGH", "MODERATE", "HIGH"])
    assert decisions[0].send
    assert decisions[2].send, "a re-escalation to HIGH must never be suppressed"
    assert decisions[2].overrode_cooldown
    assert "overrides cooldown" in decisions[2].reason


def test_persisting_high_is_rate_limited_not_silenced():
    """A long HIGH spell texts on day 1, then again once the cooldown expires."""
    decisions = run_sequence(["LOW"] + ["HIGH"] * 8)
    sent = [i for i, d in enumerate(decisions) if d.send]
    assert sent[0] == 1, "the escalation day must text"
    gaps = [b - a for a, b in zip(sent, sent[1:])]
    assert all(g >= config.SMS_COOLDOWN_DAYS for g in gaps), gaps


def test_persisting_high_does_not_text_every_day():
    decisions = run_sequence(["LOW"] + ["HIGH"] * 8)
    assert sum(d.send for d in decisions) < 8


# --------------------------------------------------------------------------
# MODERATE
# --------------------------------------------------------------------------

def test_escalation_to_moderate_texts():
    assert run_sequence(["LOW", "MODERATE"])[1].send


def test_persisting_moderate_never_texts_again():
    """A week of 'risk rising' says nothing new after the first message."""
    decisions = run_sequence(["LOW"] + ["MODERATE"] * 10)
    assert sum(d.send for d in decisions) == 1
    assert decisions[1].send
    assert "has not risen" in decisions[5].reason


def test_moderate_after_high_does_not_text():
    """Falling from HIGH to MODERATE is de-escalation, not news."""
    decisions = run_sequence(["HIGH", "MODERATE"])
    assert not decisions[1].send


def test_flapping_moderate_is_held_by_the_cooldown():
    """LOW/MODERATE alternating daily must not text every other morning."""
    decisions = run_sequence(["LOW", "MODERATE"] * 6)
    n_sent = sum(d.send for d in decisions)
    assert n_sent <= 1 + (12 // config.SMS_COOLDOWN_DAYS), \
        f"flapping produced {n_sent} texts"
    assert any("cooldown" in d.reason for d in decisions)


def test_moderate_texts_again_once_the_cooldown_expires():
    levels = ["LOW", "MODERATE"] + ["LOW"] * config.SMS_COOLDOWN_DAYS + ["MODERATE"]
    decisions = run_sequence(levels)
    assert decisions[1].send
    assert decisions[-1].send


# --------------------------------------------------------------------------
# Cooldown mechanics
# --------------------------------------------------------------------------

def test_cooldown_is_per_level_not_global():
    """A MODERATE text must not suppress a HIGH one the next day."""
    decisions = run_sequence(["LOW", "MODERATE", "HIGH"])
    assert decisions[1].send
    assert decisions[2].send


def test_cooldown_counts_calendar_days():
    policy = alerts.AlertPolicy()
    t0 = START
    assert policy.evaluate(alert_at("HIGH", t0), t0).send

    # Same level a few hours later: still day 0, must be blocked.
    later = t0 + pd.Timedelta(hours=8)
    assert not policy.decide(alert_at("HIGH", later), later).send

    just_under = t0 + (config.SMS_COOLDOWN_DAYS - 1) * DAY
    assert not policy.decide(alert_at("HIGH", just_under), just_under).send

    exactly = t0 + config.SMS_COOLDOWN_DAYS * DAY
    assert policy.decide(alert_at("HIGH", exactly), exactly).send


def test_first_ever_assessment_at_an_actionable_level_texts():
    """No prior state must not mean silence on day one."""
    assert run_sequence(["HIGH"])[0].send
    assert run_sequence(["MODERATE"])[0].send


# --------------------------------------------------------------------------
# State handling
# --------------------------------------------------------------------------

def test_decide_is_side_effect_free():
    """decide() may be called repeatedly (e.g. by the dashboard) safely."""
    policy = alerts.AlertPolicy()
    a = alert_at("HIGH", START)
    first = policy.decide(a, START)
    for _ in range(5):
        assert policy.decide(a, START).send == first.send
    assert policy.state.previous_level is None, "decide() must not record"


def test_record_updates_state():
    policy = alerts.AlertPolicy()
    a = alert_at("HIGH", START)
    decision = policy.decide(a, START)
    policy.record(a, decision, START)
    assert policy.state.previous_level == "HIGH"
    assert policy.state.last_sent_level == "HIGH"
    assert policy.state.last_sent_at == START


def test_state_can_be_supplied_for_resuming_across_restarts():
    """A deployed service restarts; the policy must not forget what it sent."""
    state = alerts.PolicyState(
        previous_level="HIGH", last_sent_at=START, last_sent_level="HIGH")
    policy = alerts.AlertPolicy(state)
    next_day = START + DAY
    assert not policy.decide(alert_at("HIGH", next_day), next_day).send


def test_every_decision_explains_itself():
    for level in ("LOW", "MODERATE", "HIGH", de.UNKNOWN):
        for d in run_sequence([level] * 5):
            assert d.reason.strip(), f"{level} produced an empty reason"


# --------------------------------------------------------------------------
# The policy must not change what the dashboard sees
# --------------------------------------------------------------------------

def test_policy_does_not_alter_the_alert():
    a = alert_at("MODERATE", START)
    before = (a.level, a.kind, dict(a.sms))
    policy = alerts.AlertPolicy()
    policy.evaluate(a, START)
    assert (a.level, a.kind, dict(a.sms)) == before
