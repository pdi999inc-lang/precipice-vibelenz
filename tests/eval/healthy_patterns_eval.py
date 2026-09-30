"""
Synthetic eval set for app/healthy_patterns.py.

Copyright © 2026 Ricky Sessums. All rights reserved.

PROVENANCE: hand-written synthetic text only. Pattern definitions draw on
Gottman (bids for connection, repair, fondness/admiration), Gable
(capitalization / active-constructive responding), and the mirror of each
concern pattern in app/subtle_patterns.py. Internal metrics only; never
marketing. Same author wrote rules and cases: regression floor, not accuracy.
"""

ALL = {"feelings_validated", "concrete_plans", "offers_to_meet", "owned_apology", "boundary_respected",
       "open_about_you", "asked_and_met", "curious_about_you", "remembers_details", "celebrates_you",
       "appreciation"}

CASES = [
    # feelings_validated
    dict(id="val_pos_1", expect={"feelings_validated"}, forbid=set(), text=
"""YOU: it hurt that you didn't text back all weekend
THEM: that's fair. I should have said I was slammed. I get why that felt bad"""),
    dict(id="val_pos_2", expect={"feelings_validated"}, forbid=set(), text=
"""YOU: I felt kind of ignored at the party
THEM: you're right, I left you alone way too long"""),
    dict(id="val_neg_1", expect=set(), forbid={"feelings_validated"}, text=
"""YOU: it hurt that you didn't text back
THEM: you're overreacting"""),
    dict(id="val_neg_2", expect=set(), forbid={"feelings_validated"}, text=
"""THEM: you're right about that movie, it was terrible
YOU: told you"""),

    # concrete_plans
    dict(id="plan_pos_1", expect={"concrete_plans"}, forbid=set(), text=
"""THEM: want to get tacos saturday at 7?
YOU: yes!"""),
    dict(id="plan_pos_2", expect={"concrete_plans"}, forbid=set(), text=
"""YOU: when are you free this week?
THEM: thursday after 6 works for me"""),
    dict(id="plan_neg_1", expect=set(), forbid={"concrete_plans"}, text=
"""YOU: dinner tomorrow?
THEM: can't tomorrow, maybe next week"""),
    dict(id="plan_neg_2", expect=set(), forbid={"concrete_plans"}, text=
"""THEM: we should hang out sometime
YOU: sure"""),

    # offers_to_meet
    dict(id="meet_pos_1", expect={"offers_to_meet"}, forbid=set(), text=
"""THEM: this is fun but I'd love to hear your voice. want to facetime tonight?"""),
    dict(id="meet_pos_2", expect={"offers_to_meet"}, forbid=set(), text=
"""THEM: we should grab a coffee this week, easier than texting"""),
    dict(id="meet_neg_1", expect=set(), forbid={"offers_to_meet"}, text=
"""YOU: want to facetime?
THEM: my camera is broken sorry"""),

    # owned_apology
    dict(id="apol_pos_1", expect={"owned_apology"}, forbid=set(), text=
"""THEM: I'm sorry for snapping at you earlier. that's on me"""),
    dict(id="apol_pos_2", expect={"owned_apology"}, forbid=set(), text=
"""THEM: I messed up by not calling. I won't do that again"""),
    dict(id="apol_neg_1", expect=set(), forbid={"owned_apology"}, text=
"""THEM: I'm sorry you feel that way"""),
    dict(id="apol_neg_2", expect=set(), forbid={"owned_apology"}, text=
"""THEM: sorry, but you started it"""),

    # boundary_respected
    dict(id="bnd_pos_1", expect={"boundary_respected"}, forbid=set(), text=
"""THEM: send me a pic?
YOU: I'm not comfortable sending pics yet
THEM: totally understand, no pressure at all"""),
    dict(id="bnd_pos_2", expect={"boundary_respected"}, forbid=set(), text=
"""YOU: can we slow down a little? this is moving fast for me
THEM: of course. take your time"""),
    dict(id="bnd_neg_1", expect=set(), forbid={"boundary_respected"}, text=
"""YOU: I'm not ready to meet yet
THEM: no worries
THEM: come on, just once, why not"""),

    # open_about_you
    dict(id="open_pos_1", expect={"open_about_you"}, forbid=set(), text=
"""THEM: I told my sister about you lol she already likes you"""),
    dict(id="open_pos_2", expect={"open_about_you"}, forbid=set(), text=
"""THEM: come to my friend's birthday dinner friday? bring a friend if you want"""),
    dict(id="open_neg_1", expect=set(), forbid={"open_about_you"}, text=
"""THEM: let's keep this between us for now"""),

    # asked_and_met
    dict(id="ask_pos_1", expect={"asked_and_met"}, forbid=set(), text=
"""YOU: it's important to me that you text if you're running late
THEM: yeah absolutely, I can do that"""),
    dict(id="ask_neg_1", expect=set(), forbid={"asked_and_met"}, text=
"""YOU: it's important to me that you text if you're running late
THEM: you're too sensitive"""),

    # curious_about_you
    dict(id="cur_pos_1", expect={"curious_about_you"}, forbid=set(), text=
"""THEM: what got you into photography?
YOU: my grandpa
THEM: that's cool. do you still have any of his cameras?"""),
    dict(id="cur_neg_1", expect=set(), forbid={"curious_about_you"}, text=
"""THEM: I went to Cabo last week
THEM: then I bought a boat
YOU: nice"""),

    # remembers_details
    dict(id="rem_pos_1", expect={"remembers_details"}, forbid=set(), text=
"""THEM: how did the interview go?? you said you were nervous"""),
    dict(id="rem_neg_1", expect=set(), forbid={"remembers_details"}, text=
"""THEM: how's it going
YOU: good"""),

    # celebrates_you
    dict(id="cel_pos_1", expect={"celebrates_you"}, forbid=set(), text=
"""YOU: I got the job!!
THEM: congrats!! tell me everything"""),
    dict(id="cel_neg_1", expect=set(), forbid={"celebrates_you"}, text=
"""YOU: I got the job!!
THEM: cool. anyway I had a rough day"""),

    # appreciation
    dict(id="app_pos_1", expect={"appreciation"}, forbid=set(), text=
"""THEM: thank you for checking on me yesterday, it means a lot"""),
    dict(id="app_neg_1", expect=set(), forbid={"appreciation"}, text=
"""YOU: thank you for dinner!
THEM: np"""),

    # attribution fail-closed
    dict(id="unlabeled", expect=set(), forbid=ALL, text=
"""want to get tacos saturday at 7?
congrats!! tell me everything"""),
    dict(id="mix", expect=set(), forbid=ALL, user_side="mix", text=
"""THEM: want to get tacos saturday at 7?"""),

    # a warm, healthy conversation: several at once
    dict(id="warm_multi", expect={"concrete_plans", "celebrates_you", "remembers_details", "appreciation"}, forbid=set(), text=
"""YOU: I passed my board exam!!
THEM: congratulations!! so proud of you
THEM: how did the last section go? you said it was the hardest
YOU: brutal but done
THEM: let's celebrate, dinner friday at 7? my treat
THEM: and thanks for listening to me vent about work last night"""),
]
