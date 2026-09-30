"""
Synthetic eval set for app/subtle_patterns.py.

Copyright © 2026 Ricky Sessums. All rights reserved.

PROVENANCE: every conversation below was written by hand for this eval. None is
copied from a real person's conversation or a public post. Pattern definitions
are grounded in public guidance (FTC, FBI IC3, FCC, ICE HSI romance-scam red
flags; emotional-invalidation and future-faking literature; fraud-victimology
research on victim rationalization). Per project rule, metrics computed on this
set are internal only and must never appear in marketing.

KNOWN BIAS: the same author wrote the rules and the cases. Treat scores here as
a regression floor, not as an accuracy estimate. Real accuracy needs rated
first-party reads.

Each case: id, expect (set of keys that MUST fire), forbid (keys that must NOT
fire), text, user_side (optional).
"""

CASES = [
    # ---------------- 1a platform migration: positives ----------------
    dict(id="pm_pos_1", expect={"platform_migration_push"}, forbid=set(), text=
"""THEM: hey beautiful, your smile is amazing
YOU: aw thank you! how's your week going
THEM: busy but better now. I'm not on here much, add me on WhatsApp?"""),
    dict(id="pm_pos_2", expect={"platform_migration_push"}, forbid=set(), text=
"""THEM: this app keeps glitching on me
THEM: easier to chat on Telegram, do you have it
YOU: I think so?"""),
    dict(id="pm_pos_3", expect={"platform_migration_push"}, forbid=set(), text=
"""THEM: I'm about to delete this app honestly
THEM: text me on whatsapp +1 555 010 2233
YOU: oh ok"""),
    dict(id="pm_pos_4", expect={"platform_migration_push"}, forbid=set(), text=
"""YOU: haha that's funny
THEM: let's move to Signal, I rarely check this app"""),
    # platform migration: hard negatives
    dict(id="pm_neg_1", expect=set(), forbid={"platform_migration_push"}, text=
"""THEM: that was so fun last night!
YOU: it really was
THEM: here's my number, easier than this app lol. dinner thursday at 7?"""),
    dict(id="pm_neg_2", expect=set(), forbid={"platform_migration_push"}, text=
"""YOU: do you use whatsapp?
THEM: no I don't really use whatsapp, here is fine"""),
    dict(id="pm_neg_3", expect=set(), forbid={"platform_migration_push"}, text=
"""THEM: I saw that meme on instagram today
YOU: which one
THEM: the dog in the sweater"""),

    # ---------------- 1b secrecy: positives ----------------
    dict(id="sec_pos_1", expect={"secrecy_request"}, forbid=set(), text=
"""THEM: I've never felt this way about anyone
THEM: let's keep this between us for now ok? your family wouldn't understand what we have"""),
    dict(id="sec_pos_2", expect={"secrecy_request"}, forbid=set(), text=
"""YOU: my sister thinks it's weird we haven't met
THEM: your friends are just jealous. don't tell your sister about the money thing"""),
    dict(id="sec_pos_3", expect={"secrecy_request"}, forbid=set(), text=
"""THEM: after you read this please delete these messages
THEM: nobody needs to know yet"""),
    # secrecy: hard negatives
    dict(id="sec_neg_1", expect=set(), forbid={"secrecy_request"}, text=
"""THEM: don't tell me you've never seen The Office
YOU: I haven't!! don't judge"""),
    dict(id="sec_neg_2", expect=set(), forbid={"secrecy_request"}, text=
"""THEM: I'm planning a surprise for my mom's birthday
THEM: I told my whole family about you btw, they want to meet you"""),

    # ---------------- 2 verification dodging: positives ----------------
    dict(id="ver_pos_1", expect={"verification_dodging"}, forbid=set(), text=
"""YOU: can we video call tonight?
THEM: my camera is broken, getting it fixed next week
YOU: we could just facetime on audio then see
THEM: the connection out here on the rig is terrible, I can't video call"""),
    dict(id="ver_pos_2", expect={"verification_dodging"}, forbid=set(), text=
"""THEM: I'm deployed right now, not allowed to video call on base
YOU: oh I understand"""),
    dict(id="ver_pos_3", expect={"verification_dodging"}, forbid=set(), text=
"""YOU: when can we meet? it's been a month
THEM: something came up with work this weekend
YOU: ok what about a video call at least
THEM: I'm not comfortable on camera yet, maybe next week"""),
    dict(id="ver_pos_4", expect={"verification_dodging"}, forbid=set(), text=
"""THEM: I work on an offshore platform, 6 weeks on
THEM: my phone screen is cracked so I can't facetime right now"""),
    # verification: hard negatives
    dict(id="ver_neg_1", expect=set(), forbid={"verification_dodging"}, text=
"""YOU: video call tonight?
THEM: ugh my camera is broken, can we do tomorrow on my laptop? 8pm?
YOU: perfect"""),
    dict(id="ver_neg_2", expect=set(), forbid={"verification_dodging"}, text=
"""THEM: something came up at work, so sorry
THEM: can we move it to saturday at 2? I'll bring the coffee"""),
    dict(id="ver_neg_3", expect=set(), forbid={"verification_dodging"}, text=
"""THEM: I just got back from deployment last month
YOU: welcome home!
THEM: thanks, want to grab food friday?"""),

    # ---------------- 5 future faking: positives ----------------
    dict(id="ff_pos_1", expect={"future_faking"}, forbid=set(), text=
"""THEM: I can see us in a little house by the lake
THEM: when we get married I'll take you to Italy
YOU: haha slow down, when can we meet first?
THEM: soon baby. we'll see after this contract"""),
    dict(id="ff_pos_2", expect={"future_faking"}, forbid=set(), text=
"""THEM: one day we'll wake up next to each other every morning
THEM: I want to grow old with you
YOU: are we still on for this weekend?
THEM: I love you so much, you're my future wife"""),
    dict(id="ff_pos_3", expect={"future_faking"}, forbid=set(), text=
"""THEM: our future is going to be amazing
THEM: I'll fly you out to meet my mom
YOU: that would be nice. what day works this month?
THEM: let's play it by ear, I don't want to rush things"""),
    # future faking: hard negatives
    dict(id="ff_neg_1", expect=set(), forbid={"future_faking"}, text=
"""THEM: someday we should do a road trip to Yellowstone
YOU: omg yes
THEM: but first, dinner saturday at 7? I'll book the Thai place"""),
    dict(id="ff_neg_2", expect=set(), forbid={"future_faking"}, text=
"""YOU: when can we meet?
THEM: tomorrow after work? 6pm at the park"""),
    dict(id="ff_neg_3", expect=set(), forbid={"future_faking"}, text=
"""THEM: I can see us being really good together
YOU: me too
THEM: coffee tuesday morning?"""),

    # ---------------- 6 concern minimized: positives ----------------
    dict(id="min_pos_1", expect={"concern_minimized"}, forbid=set(), text=
"""YOU: it hurt that you didn't show up last night and didn't text
THEM: you're overreacting, I was tired
YOU: ok"""),
    dict(id="min_pos_2", expect={"concern_minimized"}, forbid=set(), text=
"""YOU: I noticed you were still active on the app
THEM: you're reading way too much into this
THEM: honestly you're so paranoid"""),
    dict(id="min_pos_3", expect={"concern_minimized"}, forbid=set(), text=
"""YOU: can we talk about what you said to my friend?
THEM: calm down
THEM: it's not a big deal"""),
    dict(id="min_pos_4", expect={"concern_minimized"}, forbid=set(), text=
"""YOU: why did you tell me you were home when you were out?
THEM: that never happened, you're imagining things"""),
    # minimization: hard negatives
    dict(id="min_neg_1", expect=set(), forbid={"concern_minimized"}, text=
"""YOU: omw, running 5 min late sorry!!
THEM: relax I'm not even there yet lol"""),
    dict(id="min_neg_2", expect=set(), forbid={"concern_minimized"}, text=
"""YOU: I felt a little hurt about last night
THEM: I'm really sorry. that wasn't fair to you. can we talk tonight?"""),
    dict(id="min_neg_3", expect=set(), forbid={"concern_minimized"}, text=
"""YOU: ugh I spilled coffee on my shirt before the interview
THEM: it's not a big deal, nobody will notice! you got this"""),
    dict(id="min_neg_unlabeled", expect=set(), forbid={"concern_minimized"}, text=
"""it hurt that you didn't text
you're overreacting"""),

    # ---------------- 10 user rationalizing: positives ----------------
    dict(id="rat_pos_1", expect={"user_rationalizing"}, forbid=set(), text=
"""YOU: hey you never replied yesterday
YOU: I know you're busy with work though
YOU: sorry maybe I'm just overthinking
THEM: yeah"""),
    dict(id="rat_pos_2", expect={"user_rationalizing"}, forbid=set(), text=
"""YOU: I was kind of hurt you cancelled again
THEM: you're too sensitive
YOU: sorry for being needy
YOU: never mind, forget I said anything"""),
    dict(id="rat_pos_3", expect={"user_rationalizing"}, forbid=set(), text=
"""YOU: sorry to bug you
YOU: sorry again, I know you have a lot going on
YOU: I don't want to be pushy, sorry
THEM: k"""),
    # rationalizing: hard negatives
    dict(id="rat_neg_1", expect=set(), forbid={"user_rationalizing"}, text=
"""YOU: sorry I'm late replying! was at the gym
THEM: no worries
YOU: want to get dinner friday?"""),
    dict(id="rat_neg_2", expect=set(), forbid={"user_rationalizing"}, text=
"""YOU: you said you'd call and you didn't. that's the third time
YOU: I need you to be straight with me
THEM: you're right, I'm sorry"""),
    dict(id="rat_neg_mix", expect=set(), forbid={"user_rationalizing", "concern_minimized"}, user_side="mix", text=
"""YOU: I know you're busy
YOU: maybe I'm overthinking
THEM: you're overreacting"""),

    # ---------------- combos / clean control ----------------
    dict(id="combo_scam", expect={"platform_migration_push", "verification_dodging", "future_faking"}, forbid=set(), text=
"""THEM: I'm an engineer on an oil rig, contract ends in 2 months
THEM: add me on whatsapp, I rarely check this app
YOU: can we video call first?
THEM: can't video call, company does not allow cameras on the rig
THEM: when we finally meet I'll take you anywhere you want
THEM: our future together is all I think about
YOU: when can we meet though?
THEM: after this contract my love"""),
    dict(id="clean_1", expect=set(), forbid={"platform_migration_push", "secrecy_request", "verification_dodging", "future_faking", "concern_minimized", "user_rationalizing"}, text=
"""THEM: that trivia night was a blast
YOU: we crushed the music round
THEM: rematch next thursday at 8? I'll text the group
YOU: in"""),
]
