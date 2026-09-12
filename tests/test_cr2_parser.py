# SPDX-License-Identifier: GPL-3.0-or-later
"""Tests for core/cr2/cr2_parser.py — pure Python, no bpy, no Blender needed.

Covers the internal_name space-token fix (docs/handoff-shapekey-improvements.md
Phase 5): CR2Parser._parse_channel() used to truncate and drop the body of any
targetGeom channel whose internal_name contains a space.
"""

from core.cr2.cr2_parser import CR2Parser


def _actor(text, name="BODY"):
    fig = CR2Parser.parse_text(text)
    actor = fig.get_actor(name)
    assert actor is not None, f"actor {name!r} not found"
    return actor


def _wrap_channels(body: str) -> str:
    return f"""
{{
actor BODY
    {{
    channels
        {{
{body}
        }}
    }}
}}
"""


def test_space_in_internal_name_is_not_truncated():
    """The core regression case: a two-word internal_name with an inline delta."""
    actor = _actor(_wrap_channels("""
        targetGeom Blink Right
            {
            name Blink Right
            initValue 0
            deltas
                {
                numbDeltas 2
                d 0 0.1 0.0 0.0
                d 1 0.0 0.2 0.0
                }
            }
    """))
    ch = next(c for c in actor.channels if c.kind == "targetGeom")
    assert ch.internal_name == "Blink Right"
    assert ch.display_name == "Blink Right"
    assert len(ch.deltas) == 2


def test_three_word_internal_name():
    actor = _actor(_wrap_channels("""
        targetGeom Eyes Blink Right
            {
            initValue 0
            }
    """))
    ch = next(c for c in actor.channels if c.kind == "targetGeom")
    assert ch.internal_name == "Eyes Blink Right"


def test_single_word_internal_name_unaffected():
    """The common case — must not regress."""
    actor = _actor(_wrap_channels("""
        targetGeom BreastSize
            {
            initValue 0
            }
    """))
    ch = next(c for c in actor.channels if c.kind == "targetGeom")
    assert ch.internal_name == "BreastSize"


def test_bare_channel_with_no_name_token():
    """scale/rotate/etc. channels have no internal-name token at all —
    the immediate '{' case the fix must not break."""
    actor = _actor(_wrap_channels("""
        scale
            {
            initValue 1
            }
    """))
    ch = next(c for c in actor.channels if c.kind == "scale")
    assert ch.internal_name == ""


def test_channel_after_a_space_named_one_still_parses():
    """A space-named channel must not swallow the next channel in the block."""
    actor = _actor(_wrap_channels("""
        targetGeom Toes Grasp
            {
            initValue 0
            }
        targetGeom NavelGone
            {
            initValue 0
            }
    """))
    names = [c.internal_name for c in actor.channels if c.kind == "targetGeom"]
    assert names == ["Toes Grasp", "NavelGone"]


# --- fixture-backed: real CR2 files (skipped if Test_Poser_Assets/ absent) ---

def test_aiko3_full_cr2_recovers_known_space_names(asset_path):
    path = asset_path("!Aiko 3.cr2")
    fig = CR2Parser.parse_file(str(path))
    names = {
        ch.internal_name
        for actor in fig.actors
        for ch in actor.channels
        if ch.kind == "targetGeom"
    }
    for expected in ("Blink Right", "Blink Left", "Mouth F", "Tongue L"):
        assert expected in names, f"{expected!r} missing — space-in-name regression"


def test_aiko3_sp_cr2_recovers_known_space_names(asset_path):
    path = asset_path("Aiko3 All SP Morphs.cr2")
    fig = CR2Parser.parse_file(str(path))
    names = {
        ch.internal_name
        for actor in fig.actors
        for ch in actor.channels
        if ch.kind == "targetGeom"
    }
    for expected in ("Blink Right", "Smile Right", "Mouth CH"):
        assert expected in names, f"{expected!r} missing — space-in-name regression"


def test_lafemme_cr2_recovers_known_space_names(asset_path):
    path = asset_path("LaFemme Pro tmp.cr2")
    fig = CR2Parser.parse_file(str(path))
    names = {
        ch.internal_name
        for actor in fig.actors
        for ch in actor.channels
        if ch.kind == "targetGeom"
    }
    for expected in ("Arms Up-Down", "Eyes Blink", "Toes Grasp", "Thumb Morph"):
        assert expected in names, f"{expected!r} missing — space-in-name regression"


def test_use_binary_morph_flag_distinguishes_pmd_referenced_channels():
    """A channel with `useBinaryMorph 1` and no inline deltas{} block has real
    deltas -- just stored externally in a .pmd (docs/handoff-morph-injection.md
    Phase 4, not built yet) -- unlike a plain dial channel with genuinely no
    deltas at all. Both have `ch.deltas == []`; only this flag tells them apart."""
    actor = _actor(_wrap_channels("""
        targetGeom Shldr_Gap_ADJ_R
            {
            numbDeltas 25892
            useBinaryMorph 1
            }
        targetGeom PlainDial
            {
            initValue 0
            }
    """))
    by_name = {ch.internal_name: ch for ch in actor.channels}
    assert by_name["Shldr_Gap_ADJ_R"].uses_binary_morph is True
    assert by_name["Shldr_Gap_ADJ_R"].deltas == []
    assert by_name["PlainDial"].uses_binary_morph is False
