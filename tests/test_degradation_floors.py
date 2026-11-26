"""Copyright (c) 2026 Ricky Sessums. All rights reserved.

F9 degradation_floor, F10 gaslighting_floor, F11 micro_regulation_floor.

These three were deliberately left out on 2026-11-22 and recorded as "left for a
threshold discussion rather than guessed at". The reason was false positives: each
of them, done carelessly, flags ordinary couples.

  F9  insults are not the signal. Couples insult each other. The signal is
      devaluation PLUS no alternatives - "youre lucky ANYONE puts up with you",
      not "youre lucky I put up with you", which is affectionate needling.
  F10 a denial is not gaslighting. "i never said that" is one of the commonest
      TRUE sentences in an argument. Tier A requires an attack on the target's
      perception; Tier B requires a denial paired with making the asking the
      offence.
  F11 the weakest of the three, stated as such in the module. "change before we
      go" is something partners say about a stained shirt. Tier A is confined to an
      explicit permission or prohibition structure; Tier B needs a shame frame
      beside the directive.

The negative controls in this file are the gate, not a formality. If a change makes
a positive case pass by loosening a pattern, the controls are what should stop it.

Still NOT covered, by design: the subtle, deniable tier of all three. Ambiguous
framing reads the same as insecurity or an ordinary bad mood, and a floor that
adjudicates ambiguity produces false alarms on worried partners. That judgement
stays with the analysis engine.
"""
import pytest

from app.safety_floors import _FLOOR_TARGET, detect

DEG = "degradation_floor"
GAS = "gaslighting_floor"
MIC = "micro_regulation_floor"
NEW = {DEG, GAS, MIC}


def fired(text, rel="partner"):
    return detect(text, rel)


# ----------------------------------------------------------------- F9 degradation
@pytest.mark.parametrize("text", [
    "THEM: youre lucky anyone puts up with it honestly",
    "THEM: youre lucky anybody wants you",
    "THEM: nobody else would stay",
    "THEM: no one else would put up with you",
    "THEM: nobody would have you",
    "THEM: who else would want you honestly",
    "THEM: who else would put up with you",
    "THEM: im the only one who tells you the truth",
    "THEM: im the only one who is honest with you",
    "THEM: youre pathetic",
    "THEM: youre worthless",
    "THEM: youre an embarrassment",
    "THEM: youre useless",
])
def test_degradation_fires(text):
    assert DEG in fired(text), text


@pytest.mark.parametrize("text", [
    # First person, not generalised: ordinary affectionate needling.
    "THEM: youre lucky i put up with you honestly",
    "THEM: youre lucky im patient",
    # "useless" with a scope is banter about a skill, not about the person.
    "THEM: youre useless at parking lol",
    "THEM: youre useless at directions",
    # Harsh but not the no-alternatives claim.
    "THEM: that was a stupid thing to do",
    "THEM: im annoyed with you",
    "THEM: you embarrassed me tonight",
    # Praise that happens to share vocabulary.
    "THEM: im lucky anyone puts up with ME",
    "THEM: nobody else would have done that for me",
])
def test_degradation_negative_controls(text):
    assert DEG not in fired(text), text


# ----------------------------------------------------------------- F10 gaslighting
@pytest.mark.parametrize("text", [
    "THEM: that never happened",
    "THEM: youre imagining things",
    "THEM: youre imagining it",
    "THEM: youre making it up",
    "THEM: youre making this up",
    "THEM: youre remembering it wrong",
    "THEM: youre delusional",
    "THEM: youre paranoid",
    "THEM: so now youre building a case against me?",
])
def test_gaslighting_tier_a_fires_alone(text):
    assert GAS in fired(text), text


@pytest.mark.parametrize("text", [
    "THEM: i never said that\nTHEM: you always do this",
    "THEM: i didnt say that\nTHEM: youre exhausting",
    "THEM: thats not what i said\nTHEM: this is why i dont tell you things",
    "THEM: i never said that\nTHEM: youre twisting my words",
])
def test_gaslighting_tier_b_needs_denial_and_deflection(text):
    assert GAS in fired(text), text


@pytest.mark.parametrize("text", [
    # A bare denial is the commonest true sentence in an argument.
    "THEM: i never said that",
    "THEM: i didnt say that, maybe i forgot",
    "THEM: thats not what i said. i said id try",
    # Deflection alone, with nothing denied.
    "THEM: youre exhausting",
    "THEM: you always do this",
    # Excluded on purpose: far more often affectionate than a denial of reality.
    "THEM: youre crazy, thats amazing",
    "THEM: ok youre crazy haha",
    # Ordinary memory disagreement, acknowledged.
    "THEM: i genuinely dont remember saying that",
    "THEM: we remember it differently i guess",
])
def test_gaslighting_negative_controls(text):
    assert GAS not in fired(text), text


# ------------------------------------------------------------ F11 micro-regulation
@pytest.mark.parametrize("text", [
    "THEM: youre not wearing that",
    "THEM: youre not going out in that",
    "THEM: youre not going out like that",
    "THEM: you need my permission to go",
    "THEM: you have to get my permission to leave",
    "THEM: you ask me before you make plans",
    "THEM: you check with me before you go",
    "THEM: you dont go anywhere without telling me",
])
def test_micro_regulation_tier_a_fires_alone(text):
    assert MIC in fired(text), text


@pytest.mark.parametrize("text", [
    "THEM: change before we go\nTHEM: everyone there will be looking. is that what you want",
    "THEM: go change first\nTHEM: people will talk",
    "THEM: take that off\nTHEM: youre embarrassing me",
])
def test_micro_regulation_tier_b_needs_directive_and_shame(text):
    assert MIC in fired(text), text


@pytest.mark.parametrize("text", [
    # The directive alone is ordinary.
    "THEM: change before we go, its formal",
    "THEM: go change first, well be late",
    "THEM: take that off, its got paint on it",
    # Care about weather, hours or illness is phrased this way. No pattern may match it.
    "THEM: i dont want you going out in this weather",
    "THEM: i dont want you driving in the snow",
    "THEM: i dont want you going out while youre still sick",
    # Ordinary coordination, not a permission structure.
    "THEM: can you ask me before you book something",
    "THEM: let me know before you head out so i can start dinner",
    "THEM: text me when you get there",
])
def test_micro_regulation_negative_controls(text):
    assert MIC not in fired(text), text


# ------------------------------------------------------------------ shared contract
@pytest.mark.parametrize("floor", sorted(NEW))
def test_each_new_floor_targets_coercion_risk_at_sixty(floor):
    assert _FLOOR_TARGET[floor] == ("COERCION_RISK", 60), floor


@pytest.mark.parametrize("text", [
    "YOU: youre lucky anyone puts up with you",
    "YOU: that never happened",
    "YOU: youre not going out like that",
])
def test_the_users_own_lines_never_fire_a_floor(text):
    # Floors read only the other party. A user quoting or venting must not be scored
    # as the aggressor.
    assert not (set(fired(text)) & NEW), text


@pytest.mark.parametrize("text", [
    # The five conversation controls from the coercion fixture corpus: ordinary
    # conflict, boundary-setting, worry, mutual frustration and a direct ask.
    "THEM: i was annoyed you didnt text back\nTHEM: i get it. i think i was just in my head about it\nTHEM: no its ok. i could have just asked instead of stewing",
    "THEM: ok. everything alright?\nTHEM: understood. want me to take the dog so the place is quiet\nTHEM: done. text me if you change your mind, no pressure either way",
    "THEM: hey, you get home ok?\nTHEM: haha no worries. i was only checking because of the storm\nTHEM: ok good. have a good night, talk tomorrow",
    "THEM: you left the dishes again\nTHEM: thats what you said yesterday\nTHEM: im not trying to nag. it just piles up on me\nTHEM: ok. thank you",
    "THEM: id rather you didnt post that photo of us yet\nTHEM: i havent told work im seeing anyone, its a me thing not a you thing\nTHEM: thanks. im not trying to hide you, i promise",
])
def test_the_benign_corpus_controls_stay_silent(text):
    assert not (set(fired(text)) & NEW), text


def test_the_subtle_tier_is_left_to_the_engine():
    # Pins the boundary deliberately, the same way the F6 tests do. These are the
    # deniable versions of all three patterns. A floor firing here would be flagging
    # insecurity and ordinary bad moods as coercive control.
    subtle = [
        "THEM: im realistic, thats all. you froze in the last one",
        "THEM: maybe im remembering it differently than you",
        "THEM: i just think that dress is a lot for a work thing",
        "THEM: i always feel weird after you see her",
    ]
    for text in subtle:
        assert not (set(fired(text)) & NEW), text
