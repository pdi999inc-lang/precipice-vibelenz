"""Copyright © 2026 Ricky Sessums. All rights reserved.

F7 surveillance_floor, F8 financial_control_floor, and the widened physical-harm
threat patterns.

Measured 2026-11-22: of eleven coercion reads, ten fired no floor at all. The floor
layer was built for the fraud lane (three money floors) plus two narrow coercion
shapes that are themselves fraud-adjacent - sextortion and romance-scam isolation.
Partner coercion had no deterministic backstop, so every degraded read (LLM
unavailable, which was measured at 1 in 7 and once at 3 in 6) returned no signals.

Scope: covert access and one-way control only. Ordinary partner life - shared
logins, shared budgeting, asking where someone got to - must stay silent, and the
negative controls below are the gate on that.
"""
import pytest

from app.analyzer_combined import detect_physical_harm
from app.safety_floors import _FLOOR_TARGET, detect

SURV = "surveillance_floor"
FIN = "financial_control_floor"


def fired(text, rel="partner"):
    return detect(text, rel)


# ------------------------------------------------------- F7 surveillance
@pytest.mark.parametrize("text", [
    "THEM: i went through your messages",
    "THEM: i went through your phone while you were asleep",
    "THEM: i read your dms",
    "THEM: send me your passcode then",
    "THEM: give me your pin",
    "THEM: if theres nothing to hide just give me the passcode",
    "THEM: your location stopped updating at 8:40",
    "THEM: your location was off again",
    "THEM: i check your location every day",
    "THEM: from now on your phone stays on the counter at night",
], ids=["went_through_messages","went_through_phone","read_dms","demand_passcode",
        "demand_pin","nothing_to_hide","location_stopped","location_off",
        "tracks_location","device_custody"])
def test_surveillance_fires(text):
    assert SURV in fired(text)


@pytest.mark.parametrize("text", [
    "THEM: hey whats the wifi password",
    "THEM: can you send me the netflix login when you get a sec",
    "THEM: i saw your story, looked fun",
    "THEM: i read your message about dinner, sounds good",
    "THEM: sorry my phone died, where are you",
    "THEM: want me to share my location so you know im ok driving back",
    "THEM: hey, you get home ok?",
], ids=["wifi_password","shared_login","saw_story","read_a_message",
        "phone_died","offers_own_location","checkin"])
def test_ordinary_phone_talk_stays_quiet(text):
    assert SURV not in fired(text)


def test_subtle_monitoring_is_left_to_the_engine():
    # "i noticed you were active at 11" is deniable and reads the same as anxiety.
    # Deliberate boundary; change it only as a conscious widening.
    text = ("THEM: i noticed you were active at 11 though\n"
            "THEM: i just notice these things, i cant help it\n"
            "THEM: i wish you'd just tell me when youre up. i worry")
    assert SURV not in fired(text)


# --------------------------------------------------- F8 financial control
@pytest.mark.parametrize("text", [
    "THEM: i froze your card",
    "THEM: i cancelled your card until we talk",
    "THEM: i took you off the account",
    "THEM: im moving the account to my name",
    "THEM: ill give you an allowance",
    "THEM: you run things past me first",
    "THEM: youll just ask me when you need something",
    "THEM: ask me first before you spend anything",
    "THEM: and it goes into our account, which i manage. ask me next time",
], ids=["froze_card","cancelled_card","removed_from_account","seizing_account",
        "allowance","run_past_me","ask_when_you_need","ask_before_spend",
        "manages_and_permission"])
def test_financial_control_fires(text):
    assert FIN in fired(text)


@pytest.mark.parametrize("text", [
    "THEM: can we check with each other before any big purchases",
    "THEM: i froze my card, think it got skimmed",
    "THEM: can you ask me before you book anything so we dont double up",
    "THEM: i handle the bins, you handle the washing",
], ids=["mutual_budgeting","froze_own_card","ask_before_booking","chores"])
def test_ordinary_money_talk_stays_quiet(text):
    assert FIN not in fired(text)


def test_subtle_financial_pressure_is_left_to_the_engine():
    text = ("THEM: i just think we should be checking with each other. im the one tracking it all\n"
            "THEM: and yet here we are talking about a coat")
    assert FIN not in fired(text)


def test_money_context_alone_does_not_fire():
    # Tier B needs BOTH halves. A bare mention of an account is not control.
    assert FIN not in fired("THEM: the account balance came through, all good")


def test_permission_phrase_alone_does_not_fire():
    assert FIN not in fired("THEM: ask me first before you tell anyone about the surprise")


# --------------------------------------------- widened physical-harm threats
@pytest.mark.parametrize("text", [
    "if you leave i will hurt you",
    "im gonna hit you",
    "ill put you in the hospital",
    "i will choke you",
    "he threatened to hurt me",
], ids=["hurt","hit","hospital","choke","threatened_to_hurt"])
def test_harm_threats_are_detected(text):
    assert detect_physical_harm(text), text


@pytest.mark.parametrize("text", [
    "ill hit you up later",
    "ill beat you at chess",
    "ill touch base with you tomorrow",
    "i will hit the gym after work",
    "youre gonna beat the deadline",
], ids=["hit_you_up","beat_at_chess","touch_base","hit_the_gym","beat_deadline"])
def test_ordinary_phrasing_is_not_a_harm_threat(text):
    assert not detect_physical_harm(text), text


def test_harm_disclosure_still_detected():
    # The original disclosure shape must be unaffected by the threat widening.
    assert detect_physical_harm("he put his hands on me")


# --------------------------------------------------------------- wiring
@pytest.mark.parametrize("floor", [SURV, FIN])
def test_floors_target_the_coercion_lane(floor):
    assert _FLOOR_TARGET[floor] == ("COERCION_RISK", 60)


@pytest.mark.parametrize("floor", [SURV, FIN])
def test_floors_count_as_coercion_floors_for_lane_correction(floor):
    # Without this, a surveillance or financial read that also mentions money would
    # be relabelled FRAUD by the lane correction.
    import inspect

    from app import safety_floors
    src = inspect.getsource(safety_floors.apply_safety_floors)
    block = src.split("_coercion_floors", 1)[1].split("}", 1)[0]
    assert floor in block


def test_existing_floors_still_work():
    # F3 needs both halves: a secrecy phrase and a dismissal of outsiders. Note that
    # F3_ISOLATE matches "theyre jealous" but not "your friends are jealous" - that
    # narrowness is pre-existing F3 behaviour, recorded here rather than changed.
    assert "isolation_secrecy_floor" in fired(
        "THEM: lets keep this between us for now\nTHEM: theyre just jealous, they dont understand what we have")
    assert "partner_isolation_floor" in fired("THEM: its me or her, figure it out")
