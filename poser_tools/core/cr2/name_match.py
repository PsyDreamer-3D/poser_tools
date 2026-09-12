# SPDX-License-Identifier: MIT
"""
name_match.py
-------------
Resolve a morph name to the full set of CR2 targetGeom channels that
constitute it.

A morph name almost never identifies a single Channel: Poser declares a
body-wide morph (e.g. "Pregnant", "PBMFullFigure") once per actor — hip,
chest, every finger, ... — each holding that actor's slice of the full
mesh delta. Real base-figure CR2s checked while building this had morphs
split across 50+ actors under one name. So the unit of work here is a
name -> group of Channels, not name -> one Channel.

Which of a channel's two names (internal_name vs display_name) an FBX
exporter picks for the baked shape key varies per channel, with no fixed
rule (see docs/handoff-shapekey-improvements.md Phase 5) -- but within one
figure, no display_name is shared by two distinct internal_names, so
falling back from internal_name to display_name carries no ambiguity risk.

Deliberately agnostic of Blender's ".NNN" shape-key dedup suffix -- that's
an FBX/Blender concern, not a Poser-format one. Callers strip it before
calling match_channel_group(), the same way the existing FBX-name
heuristic (core/functionsShapeKeys.get_parent_name()) does today.
"""

from .cr2_parser import Figure


def build_channel_index(figure: Figure) -> dict:
    """Map every targetGeom channel name to its full cross-actor channel group.

    Returns:
        {
            "by_internal": {internal_name: [Channel, ...]},
            "by_display": {display_name: internal_name},
        }

    "by_internal" groups every actor's Channel for a given internal_name --
    the complete per-actor split of one full-body morph. "by_display" is a
    secondary index resolving a channel's display_name back to the
    internal_name of its group (first one seen wins if a figure is
    inconsistent, which does happen -- see test_disagreeing_display_names).
    """
    by_internal: dict = {}
    by_display: dict = {}

    for actor in figure.actors:
        for ch in actor.channels:
            if ch.kind != "targetGeom":
                continue
            by_internal.setdefault(ch.internal_name, []).append(ch)
            if ch.display_name and ch.display_name not in by_display:
                by_display[ch.display_name] = ch.internal_name

    return {"by_internal": by_internal, "by_display": by_display}


def match_channel_group(index: dict, name: str) -> list:
    """Resolve `name` to its channel group via the index from build_channel_index().

    Tries `by_internal` first, then `by_display` -> `by_internal`. `name`
    should already have any Blender ".NNN" dedup suffix stripped by the
    caller. Returns [] if neither lookup hits.
    """
    group = index["by_internal"].get(name)
    if group is not None:
        return group

    internal_name = index["by_display"].get(name)
    if internal_name is not None:
        return index["by_internal"].get(internal_name, [])

    return []
