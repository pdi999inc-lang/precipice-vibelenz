# Copyright © 2026 Ricky Sessums. All rights reserved.
"""app/screenshot_merge.py: overlapping screenshots never produce duplicated lines."""
from app.screenshot_merge import merge_chunks


def test_forward_overlap_dropped():
    a = "THEM: Oh that's nice\nYOU: I'm going back to get my stuff in three weeks\nYOU: Why would you have stayed?"
    b = "YOU: Why would you have stayed?\nTHEM: I would just feel at peace being away from family"
    out, info = merge_chunks([a, b])
    assert out[1] == "THEM: I would just feel at peace being away from family"
    assert info["overlaps_removed"] == 1 and info["pairs"][0]["where"] == "head"


def test_reverse_order_upload_overlap_dropped():
    newer = "YOU: Why would you have stayed?\nTHEM: I would just feel at peace being away from family"
    older = "THEM: Oh that's nice\nYOU: Why would you have stayed?"
    out, info = merge_chunks([newer, older])
    assert out[1] == "THEM: Oh that's nice" and info["pairs"][0]["where"] == "tail"


def test_multi_line_overlap_and_cropped_edge_bubble():
    a = "THEM: Drink\nYOU: I can have whatever you want here\nTHEM: Gotta figure out what to do with my kiddos lol"
    b = "YOU: I can have whatever you want\nTHEM: Gotta figure out what to do with my kiddos lol\nYOU: I understand."
    out, info = merge_chunks([a, b])
    assert out[1] == "YOU: I understand." and info["overlaps_removed"] == 2


def test_short_single_line_not_dropped():
    a = "YOU: Did you click on it?\nTHEM: Yes"
    b = "THEM: Yes\nYOU: AI crap huh?"
    out, info = merge_chunks([a, b])
    assert out[1].startswith("THEM: Yes") and info["overlaps_removed"] == 0


def test_speaker_must_match():
    a = "YOU: I really liked it there honestly"
    b = "THEM: I really liked it there honestly\nYOU: ok"
    out, info = merge_chunks([a, b])
    assert info["overlaps_removed"] == 0


def test_interior_repeat_untouched():
    a = "YOU: Why would you have stayed?\nTHEM: peace\nYOU: cool"
    b = "THEM: hi\nYOU: Why would you have stayed?\nTHEM: again"
    out, info = merge_chunks([a, b])
    assert info["overlaps_removed"] == 0 and out[1] == b


def test_single_chunk_and_errors():
    assert merge_chunks(["YOU: hi"])[0] == ["YOU: hi"]
    out, info = merge_chunks([None, "YOU: hi"])
    assert info["overlaps_removed"] == 0
