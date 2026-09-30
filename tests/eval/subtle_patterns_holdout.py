"""Blind holdout. Written after the rules were frozen; NOT used for tuning.
Copyright © 2026 Ricky Sessums. All rights reserved. Synthetic text only."""
CASES = [
 dict(id="h_pm", expect={"platform_migration_push"}, forbid=set(), text="THEM: do u have wa? my number is +44 7700 900123, i barely log in here"),
 dict(id="h_pm2", expect={"platform_migration_push"}, forbid=set(), text="THEM: hangouts is better for me, this site drains my battery"),
 dict(id="h_sec", expect={"secrecy_request"}, forbid=set(), text="THEM: this stays with us okay? people can be so judgmental about online love"),
 dict(id="h_ver", expect={"verification_dodging"}, forbid=set(), text="YOU: facetime?\nTHEM: not tonight, im a mess lol\nYOU: tomorrow?\nTHEM: my data plan doesnt cover video out here\nYOU: ok..\nTHEM: soon i promise"),
 dict(id="h_ff", expect={"future_faking"}, forbid=set(), text="THEM: you're going to love the ring I picked out\nTHEM: we'll have the wedding on the beach\nYOU: we haven't even met lol. next saturday?\nTHEM: babe you know my schedule is crazy right now"),
 dict(id="h_min", expect={"concern_minimized"}, forbid=set(), text="YOU: why were you texting your ex at 2am\nTHEM: lol here we go again\nTHEM: you always do this"),
 dict(id="h_rat", expect={"user_rationalizing"}, forbid=set(), text="YOU: you didn't have to yell like that\nYOU: I mean I get it, work has been rough on you\nYOU: it's fine, I shouldn't have pushed"),
 dict(id="h_neg_min", expect=set(), forbid={"concern_minimized"}, text="YOU: I'm so nervous about the exam\nTHEM: you're overthinking it in the best way, you studied so hard!"),
 dict(id="h_neg_pm", expect=set(), forbid={"platform_migration_push"}, text="THEM: we met at Sarah's party remember? add me on instagram so you can see the pics"),
]
