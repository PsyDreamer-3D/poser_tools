# SPDX-License-Identifier: GPL-3.0-or-later
"""Tests for core/cr2/cr2_parser.py — pure Python, no bpy, no Blender needed.

Covers the internal_name space-token fix (docs/handoff-shapekey-improvements.md
Phase 5): CR2Parser._parse_channel() used to truncate and drop the body of any
targetGeom channel whose internal_name contains a space.
"""

import os

import pytest

from core.cr2.cr2_parser import CR2Parser, resolve_geom_file


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


def test_resolve_geom_file_resolves_figureresfile_to_real_obj(tmp_path):
    """resolve_geom_file() (core/cr2/runtime_index.py-adjacent morph-injection
    UX: pick a figure's CR2, derive its reference OBJ automatically) used to
    silently no-op -- it imported a PoserPathResolver from a core/cr2/obj_loader.py
    module that doesn't exist in poser_tools (only cr2_importer has that file).
    Confirms the fix (routing through poser_paths.resolve_poser_path instead)
    actually resolves a real path."""
    root = tmp_path / "MyPoserLib"
    cr2_path = root / "Runtime" / "libraries" / "Character" / "MyFigure.cr2"
    cr2_path.parent.mkdir(parents=True)

    obj_path = root / "Runtime" / "Geometries" / "MyFigure" / "MyFigure.obj"
    obj_path.parent.mkdir(parents=True)
    obj_path.write_text("")

    figure = CR2Parser.parse_text(
        "{ figureResFile :Runtime:Geometries:MyFigure:MyFigure.obj }",
        source_file=str(cr2_path),
    )
    assert figure.geom_file == ":Runtime:Geometries:MyFigure:MyFigure.obj"

    resolve_geom_file(figure, str(cr2_path))
    assert figure.geom_file == str(obj_path.resolve())


def test_resolve_geom_file_leaves_unresolvable_path_untouched(tmp_path):
    cr2_path = tmp_path / "MyPoserLib" / "Runtime" / "libraries" / "MyFigure.cr2"
    cr2_path.parent.mkdir(parents=True)

    figure = CR2Parser.parse_text(
        "{ figureResFile :Runtime:Geometries:Missing.obj }",
        source_file=str(cr2_path),
    )
    resolve_geom_file(figure, str(cr2_path))
    assert figure.geom_file == ":Runtime:Geometries:Missing.obj"


# ---- material names with spaces (Phase 8: 41 of the owner's MAT files read nothing) --------

def _mat_file(*blocks):
    return "{\nversion\n\t{\n\tnumber 4.01\n\t}\nfigure\n\t{\n" + "".join(blocks) + "\t}\n}\n"


def _mat(name, kd="0.5 0.25 0.125 1"):
    return f"\tmaterial {name}\n\t\t{{\n\t\tKdColor {kd}\n\t\ttextureMap NO_MAP\n\t\t}}\n"


def test_material_name_with_spaces_is_read_whole():
    figure = CR2Parser.parse_text(_mat_file(_mat("Quilt Trim"), _mat("Skin Torso Main")))
    assert list(figure.materials) == ["Quilt Trim", "Skin Torso Main"]
    assert figure.materials["Quilt Trim"].diffuse_color[:3] == (0.5, 0.25, 0.125)


def test_single_word_material_names_are_unchanged():
    figure = CR2Parser.parse_text(_mat_file(_mat("SkinTorso"), _mat("Lips")))
    assert list(figure.materials) == ["SkinTorso", "Lips"]


def test_a_material_line_without_a_block_does_not_swallow_the_next_material():
    text = _mat_file("\tmaterial Orphan\n", _mat("SkinTorso"))
    assert "SkinTorso" in CR2Parser.parse_text(text).materials


_BED_MATS = ("/run/media/jess-green/236a91c9-2598-40dd-b968-9c0f0be8a903/Artwork/Poser Content/"
             "Scenes, Props and Objects/Runtime/libraries/Pose/Bed Mats/Quilt Trim 04.pz2")


@pytest.mark.skipif(not os.path.isfile(_BED_MATS), reason="Poser content drive not mounted")
def test_real_prop_mat_with_a_spaced_material_name():
    from core.cr2.poser_io import read_poser_file
    assert list(CR2Parser.parse_text(read_poser_file(_BED_MATS)).materials) == ["Quilt Trim"]


def test_a_bare_file_line_in_a_shader_node_does_not_end_the_material_early():
    node_input = ('\t\t\t\tnodeInput "Quality"\n\t\t\t\t\t{\n\t\t\t\t\tname "Quality"\n'
                  '\t\t\t\t\tvalue 0.2 0 1\n\t\t\t\t\tnode NO_NODE\n\t\t\t\t\tfile\n\t\t\t\t\t}\n')
    first = ('\tmaterial scope\n\t\t{\n\t\tKdColor 1 1 1 1\n\t\tshaderTree\n\t\t\t{\n'
             '\t\t\tnode "poser" "PoserSurface"\n\t\t\t\t{\n' + node_input + '\t\t\t\t}\n\t\t\t}\n\t\t}\n')
    figure = CR2Parser.parse_text(_mat_file(first, _mat("barrel")))
    assert list(figure.materials) == ["scope", "barrel"]
    assert figure.materials["scope"].shader_tree.nodes
