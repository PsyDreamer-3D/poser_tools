# SPDX-License-Identifier: GPL-3.0-or-later
"""Tests for core/cr2/name_match.py.

Covers the structural finding from scoping this (docs/handoff-shapekey-improvements.md
Phase 5): a morph name identifies a group of per-actor Channels, not one Channel,
and internal_name/display_name fallback carries no collision risk within a figure.

A group is a list of (Actor, Channel) pairs, not bare Channels -- applying a
channel's deltas (docs/handoff-morph-injection.md Phase 3) needs to know which
actor's geometry each Channel belongs to.
"""

from core.cr2.cr2_parser import CR2Parser
from core.cr2.name_match import build_channel_index, match_channel_group


def _figure(text: str):
    return CR2Parser.parse_text(text)


def _wrap_actors(actors_body: str) -> str:
    return f"""
{{
{actors_body}
}}
"""


def _actor_block(name: str, channels_body: str) -> str:
    return f"""
actor {name}
    {{
    channels
        {{
{channels_body}
        }}
    }}
"""


def test_name_declared_on_multiple_actors_groups_together():
    fig = _figure(_wrap_actors(
        _actor_block("hip", 'targetGeom Pregnant\n{\ninitValue 0\n}')
        + _actor_block("chest", 'targetGeom Pregnant\n{\ninitValue 0\n}')
        + _actor_block("waist", 'targetGeom Pregnant\n{\ninitValue 0\n}')
    ))
    index = build_channel_index(fig)
    group = match_channel_group(index, "Pregnant")
    assert len(group) == 3
    assert {ch.internal_name for _actor, ch in group} == {"Pregnant"}
    assert {actor.name for actor, _ch in group} == {"hip", "chest", "waist"}


def test_match_via_display_name_falls_back_to_internal_group():
    fig = _figure(_wrap_actors(
        _actor_block("hip", 'targetGeom PBMFullFigure\n{\nname FullFigure\ninitValue 0\n}')
        + _actor_block("chest", 'targetGeom PBMFullFigure\n{\nname FullFigure\ninitValue 0\n}')
    ))
    index = build_channel_index(fig)
    # Not present in by_internal at all -- must come from the display-name fallback.
    assert "FullFigure" not in index["by_internal"]
    group = match_channel_group(index, "FullFigure")
    assert len(group) == 2
    assert all(ch.internal_name == "PBMFullFigure" for _actor, ch in group)


def test_disagreeing_display_names_still_group_by_internal_name():
    """Real figures aren't consistent: one actor's display_name for a shared
    internal_name can differ from a sibling's (seen on Aiko3's HdStylized:
    internal_name='HdStylized', display_name='HdStylized' on one actor but
    'pHdStylized' on others). The group must still form under internal_name."""
    fig = _figure(_wrap_actors(
        _actor_block("head", 'targetGeom HdStylized\n{\nname HdStylized\ninitValue 0\n}')
        + _actor_block("neck", 'targetGeom HdStylized\n{\nname pHdStylized\ninitValue 0\n}')
    ))
    index = build_channel_index(fig)
    group = match_channel_group(index, "HdStylized")
    assert len(group) == 2
    # display_name fallback still resolves to the same group either way.
    assert match_channel_group(index, "pHdStylized") == group


def test_unknown_name_returns_empty_list():
    fig = _figure(_wrap_actors(_actor_block("hip", 'targetGeom Pregnant\n{\ninitValue 0\n}')))
    index = build_channel_index(fig)
    assert match_channel_group(index, "NotAThing") == []


def test_non_targetgeom_channels_are_excluded():
    fig = _figure(_wrap_actors(_actor_block("hip", """
        targetGeom Pregnant
            {
            initValue 0
            }
        rotateX rx
            {
            initValue 0
            }
    """)))
    index = build_channel_index(fig)
    assert "rx" not in index["by_internal"]
    assert match_channel_group(index, "Pregnant") != []


def test_group_entries_pair_each_channel_with_its_owning_actor():
    # The whole point of the (Actor, Channel) shape: two actors can declare
    # channels with identical internal_name but different display_name, and
    # each entry must still carry the actor it actually came from.
    fig = _figure(_wrap_actors(
        _actor_block("hip", 'targetGeom Pregnant\n{\ninitValue 0\n}')
        + _actor_block("chest", 'targetGeom Pregnant\n{\ninitValue 0\n}')
    ))
    index = build_channel_index(fig)
    group = match_channel_group(index, "Pregnant")
    by_actor = {actor.name: ch for actor, ch in group}
    assert set(by_actor) == {"hip", "chest"}
    assert all(ch.internal_name == "Pregnant" for ch in by_actor.values())


# --- fixture-backed: real CR2 files (skipped if Test_Poser_Assets/ absent) ---

def test_lafemme_pregnant_spans_known_actors(asset_path):
    path = asset_path("LaFemme Pro tmp.cr2")
    fig = CR2Parser.parse_file(str(path))
    index = build_channel_index(fig)
    group = match_channel_group(index, "Pregnant")
    assert {ch.internal_name for _actor, ch in group} == {"Pregnant"}  # one group, one name
    assert len(group) >= 4  # known to span BODY + several torso actors (abdomen/waist/hip/chest)
    # Actor identity must actually be usable, not just present -- every entry
    # should resolve to a distinct actor (no two entries collapsed onto one).
    assert len({actor.name for actor, _ch in group}) == len(group)


def test_aiko3_full_figure_spans_many_actors(asset_path):
    path = asset_path("!Aiko 3.cr2")
    fig = CR2Parser.parse_file(str(path))
    index = build_channel_index(fig)
    # 'pFullFigure' is a display_name seen on many actors in this file;
    # confirm the fallback groups them all under one internal_name.
    group = match_channel_group(index, "pFullFigure")
    assert len(group) > 10
    assert len({actor.name for actor, _ch in group}) == len(group)
