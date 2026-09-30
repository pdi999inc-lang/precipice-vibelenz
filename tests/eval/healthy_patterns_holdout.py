"""Blind holdout for healthy patterns; written after rules froze, NOT used for tuning.
Copyright © 2026 Ricky Sessums. All rights reserved. Synthetic text only."""
CASES = [
 dict(id="h_val", expect={"feelings_validated"}, forbid=set(), text="YOU: I was upset you forgot my birthday\nTHEM: ugh I hate that I did that. you had every reason to be upset"),
 dict(id="h_plan", expect={"concrete_plans"}, forbid=set(), text="THEM: I got us a table at Luigi's for 8 tomorrow"),
 dict(id="h_meet", expect={"offers_to_meet"}, forbid=set(), text="THEM: honestly I'd rather talk face to face, free for a walk sunday?"),
 dict(id="h_apol", expect={"owned_apology"}, forbid=set(), text="THEM: I was out of line last night. no excuse for it"),
 dict(id="h_bnd", expect={"boundary_respected"}, forbid=set(), text="YOU: I'd like to keep things slow\nTHEM: works for me, I'm not in a hurry"),
 dict(id="h_cel", expect={"celebrates_you"}, forbid=set(), text="YOU: I finally finished my thesis\nTHEM: WHAT. that's incredible, how does it feel??"),
 dict(id="h_neg_plan", expect=set(), forbid={"concrete_plans"}, text="THEM: let's do something saturday. actually wait I have that thing, never mind"),
 dict(id="h_neg_apol", expect=set(), forbid={"owned_apology"}, text="THEM: I'm sorry for whatever I did to make you this upset"),
]
