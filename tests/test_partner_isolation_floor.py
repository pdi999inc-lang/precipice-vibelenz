"""Copyright © 2026 Ricky Sessums. All rights reserved.

F6 partner_isolation_floor.

Why it exists: F3 (isolation_secrecy_floor) requires secrecy language AND dismissal
of outsiders, which is the romance-scam shape. Partner isolation carries no secrecy -
the other relationships are attacked openly - so F3 fired on none of the partner
isolation reads measured 2026-11-22, including an explicit "it's them or me". With no
floor firing, a degraded read (LLM unavailable) returned "No signals detected".

Scope on purpose: the floor catches unambiguous coercive isolation only. Early
ambiguous framing stays with the analysis engine, which has context a regex does not.
test_subtle_framing_is_left_to_the_engine pins that boundary so a later widening is a
deliberate decision rather than an accident.
"""
import pytest

from app.safety_floors import _FLOOR_TARGET, detect

PARTNER = "partner"
FLOOR = "partner_isolation_floor"


def fired(text, rel=PARTNER):
    return detect(text, rel)


# ----------------------------------------------------------------- fires
@pytest.mark.parametrize("text", [
    "THEM: its me or her, figure it out",
    "THEM: its them or me, it always has been",
    "THEM: so its your sister or me, choose",
    "THEM: youre going to have to choose between me and your friends",
    "THEM: shes been turning you against me since we met",
    "THEM: your mother poisoned you against me",
    "THEM: if you actually cared about us youd stop seeing her",
    "THEM: if you really cared youd quit talking to them",
    "THEM: i dont want you seeing her anymore",
    "THEM: i forbid you to talk to your brother",
], ids=["ultimatum_her","ultimatum_them","ultimatum_sister","choose_between",
        "turning_against","poisoned_against","loyalty_test","loyalty_test_quit",
        "prohibition","forbid"])
def test_unambiguous_partner_isolation_fires(text):
    assert FLOOR in fired(text)


def test_the_measured_moderate_case_fires():
    # Returned BENIGN/LOW with three suggested replies before this floor existed.
    text = ("THEM: again? you see her more than me\n"
            "THEM: she doesnt like me, you know that right\n"
            "THEM: im not telling you what to do. but if you actually cared about us "
            "youd stop seeing her\n"
            "THEM: everything i ask is unfair to you now. its me or her, figure it out")
    assert FLOOR in fired(text)


def test_the_measured_overt_case_fires():
    text = ("THEM: and shes been turning you against me since we met\n"
            "THEM: dont go. im serious\n"
            "THEM: because i cant trust you around them. its them or me, it always has been")
    assert FLOOR in fired(text)


# ------------------------------------------------------- does NOT fire
@pytest.mark.parametrize("text", [
    "THEM: whos driving tomorrow, me or her",
    "THEM: do you want me or her to pick up the kids",
    "THEM: i was annoyed you didnt text back\nTHEM: i could have just asked instead of stewing",
    "THEM: ok. everything alright?\nTHEM: text me if you change your mind, no pressure either way",
    "THEM: hey, you get home ok?\nTHEM: i was only checking because of the storm",
    "THEM: you left the dishes again\nTHEM: im not trying to nag",
    "THEM: id rather you didnt post that photo of us yet\nTHEM: its a me thing not a you thing",
], ids=["logistics_driving","logistics_pickup","conflict_repair","boundary_respected",
        "anxious_checkin","mutual_frustration","direct_ask_with_reason"])
def test_ordinary_conversation_does_not_fire(text):
    assert FLOOR not in fired(text)


def test_a_friend_advising_about_a_bad_relationship_does_not_fire():
    # A supportive friend saying "stop seeing him" is the inverse of coercion.
    text = ("THEM: honestly i think you should stop seeing him, he makes you miserable\n"
            "THEM: im saying it because i care about you")
    assert FLOOR not in fired(text, "friend")


def test_subtle_framing_is_left_to_the_engine():
    # Deliberate scope boundary, not an oversight: insecurity and early control read
    # the same in isolation, and a regex cannot weigh the context that separates them.
    # Change this test only as a conscious decision to widen the floor.
    text = ("THEM: i just always feel weird after you see her\n"
            "THEM: you come back different. more distant\n"
            "THEM: maybe its me. i just miss how we were before she moved back")
    assert FLOOR not in fired(text)


def test_only_the_other_partys_lines_are_scanned():
    # The user quoting what was said to them must not fire the floor on their own words.
    assert FLOOR not in fired("YOU: he keeps saying its me or her\nTHEM: how was your day")


# ------------------------------------------------------------ wiring
def test_floor_targets_the_coercion_lane():
    assert _FLOOR_TARGET[FLOOR] == ("COERCION_RISK", 60)


def test_floor_counts_as_a_coercion_floor_for_lane_correction():
    # The FRAUD lane-correction moves COERCION_RISK to FRAUD when only money floors
    # fired. This floor must be recognised as a coercion floor or an isolation read
    # that also mentions money would be relabelled FRAUD.
    import inspect

    from app import safety_floors
    src = inspect.getsource(safety_floors.apply_safety_floors)
    block = src.split("_coercion_floors", 1)[1].split("}", 1)[0]
    assert FLOOR in block


def test_the_romance_scam_floor_still_works():
    # F3 must be unaffected by F6.
    text = ("THEM: lets keep this between us for now\n"
            "THEM: your friends are just jealous, they dont understand what we have")
    assert "isolation_secrecy_floor" in fired(text)
