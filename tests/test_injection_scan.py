# SPDX-License-Identifier: GPL-3.0-or-later
"""Tests for core/cr2/injection_scan.py: the cheap package listing the morph picker uses.

The scan is a second reader of a format CR2Parser already reads, so most of
these assert the two agree.
"""

import os

import pytest

from core.cr2.cr2_parser import CR2Parser
from core.cr2.injection_package import load_injection_package
from core.cr2.injection_scan import scan_channels, scan_injection_package
from core.cr2.name_match import build_channel_index, display_name_for

_REAL_CONTENT_ROOT = (
    "/run/media/jess-green/236a91c9-2598-40dd-b968-9c0f0be8a903/Artwork/Poser Content"
)


def _channel(internal, body="", deltas=0, indent="\t\t\t"):
    d = "".join(f"{indent}\t\td {i} 0 0 0.001\n" for i in range(deltas))
    block = f"{indent}deltas\n{indent}\t{{\n{d}{indent}\t}}\n" if deltas else ""
    return (f"{indent}targetGeom {internal}\n{indent}\t{{\n{body}"
            f"{indent}\tindexes {deltas}\n{indent}\tnumbDeltas 38284\n{block}{indent}\t}}\n")


def _actor(name, *channels, kind="actor", block="channels"):
    return (f"\t{kind} {name}\n\t\t{{\n\t\t{block}\n\t\t\t{{\n{''.join(channels)}\t\t\t}}\n\t\t}}\n")


def _file(*actors):
    return "{\n\tversion\n\t\t{\n\t\tnumber 4.01\n\t\t}\n" + "".join(actors) + "}\n"


def _as_tuples(text):
    return [(c.internal_name, c.display_name, c.delta_count, c.uses_binary_morph)
            for c in scan_channels(text)]


def _parser_tuples(text):
    fig = CR2Parser.parse_text(text)
    return [(c.internal_name, c.display_name, len(c.deltas), c.uses_binary_morph)
            for a in fig.actors for c in a.channels if c.kind == "targetGeom"]


def test_reads_name_delta_count_and_order():
    text = _file(_actor("neck:1", _channel("PBMDC_01", "\t\t\t\tname pEarDeer\n", deltas=2)),
                 _actor("head:1", _channel("PBMDC_01", "\t\t\t\tname EarDeer\n", deltas=5)))
    assert _as_tuples(text) == [("PBMDC_01", "pEarDeer", 2, False), ("PBMDC_01", "EarDeer", 5, False)]
    assert _as_tuples(text) == _parser_tuples(text)


def test_a_nested_block_before_the_deltas_does_not_hide_the_channel():
    # Real injection files put `keys { ... }` ahead of `deltas`; a flat pattern that
    # stopped at the first brace found no channels in ~1 in 3 of them.
    body = "\t\t\t\tname EyeScale\n\t\t\t\tkeys\n\t\t\t\t\t{\n\t\t\t\t\tstatic 0\n\t\t\t\t\tk 0 0\n\t\t\t\t\t}\n"
    text = _file(_actor("head:1", _channel("EyeScale", body, deltas=3)))
    assert _as_tuples(text) == [("EyeScale", "EyeScale", 3, False)]
    assert _as_tuples(text) == _parser_tuples(text)


def test_names_with_spaces_and_repeated_spaces():
    text = _file(_actor("head:1",
                        _channel("Blink Right", "\t\t\t\tname Blink Right\n", deltas=1),
                        _channel("R bang  length", "\t\t\t\tname EMPTY-OW (OAt)\n")))
    assert _as_tuples(text) == [("Blink Right", "Blink Right", 1, False),
                                ("R bang length", "EMPTY-OW (OAt)", 0, False)]
    assert _as_tuples(text) == _parser_tuples(text)


def test_only_channels_the_parser_keeps_are_listed():
    # Pose files hold targetGeom keyframe blocks under `ikchannels` or outside any actor;
    # neither carries deltas and Apply can't use them.
    stray = "\ttargetGeom Loose\n\t\t{\n\t\tname Loose\n\t\t}\n"
    text = _file(_actor("head:1", _channel("Real", "\t\t\t\tname Real\n", deltas=1)),
                 _actor("hip:1", _channel("InIk"), block="ikchannels"),
                 stray,
                 _actor("figureHair", _channel("OnProp"), kind="prop"))
    assert [n for n, *_ in _as_tuples(text)] == ["Real", "OnProp"]
    assert _as_tuples(text) == _parser_tuples(text)


def test_binary_morph_reports_zero_deltas_like_the_parser():
    # `indexes` counts deltas that live in the .pmd; the file itself has no `d` lines.
    body = "\t\t\t\tname BodyBulk\n\t\t\t\tuseBinaryMorph 1\n"
    text = _file(_actor("hip:1", _channel("BodyBulk", body, deltas=0).replace("indexes 0", "indexes 359")))
    assert _as_tuples(text) == [("BodyBulk", "BodyBulk", 0, True)]
    assert _as_tuples(text) == _parser_tuples(text)


def test_unlabelled_channel_and_crlf_line_endings():
    text = _file(_actor("head:1", _channel("PBMDC_07", deltas=1))).replace("\n", "\r\n")
    assert _as_tuples(text) == [("PBMDC_07", "", 1, False)]


def test_quoted_name_loses_its_quotes():
    text = _file(_actor("head:1", _channel("X", '\t\t\t\tname "Big Ear"\n')))
    assert _as_tuples(text) == [("X", "Big Ear", 0, False)]


def test_malformed_text_does_not_raise():
    assert scan_channels("") == []
    assert scan_channels("}}}{{{") == []
    assert scan_channels("{\n actor a\n {\n channels\n {\n targetGeom X\n {\n name X\n") == []


def _runtime(tmp_path):
    folder = tmp_path / "Runtime" / "libraries" / "pose"
    folder.mkdir(parents=True)
    return folder


def test_a_morph_declared_in_two_files_lists_both_and_takes_the_main_actors_label(tmp_path):
    # Real "All Head Morphs" orchestrators split each morph into a head file and a partial.
    folder = _runtime(tmp_path)
    (folder / "InjDeltas.pEarDeer.pz2").write_text(
        _file(_actor("neck:1", _channel("PBMDC_01", "\t\t\t\tname pEarDeer\n", deltas=2))))
    (folder / "InjDeltas.EarDeer.pz2").write_text(
        _file(_actor("head:1", _channel("PBMDC_01", "\t\t\t\tname EarDeer\n", deltas=9))))
    (folder / "InjDeltas.Other.pz2").write_text(
        _file(_actor("head:1", _channel("PBMDC_02", "\t\t\t\tname Other\n", deltas=1))))
    orchestrator = folder / "! All.pz2"
    orchestrator.write_text(
        '{\nreadScript ":Runtime:libraries:pose:InjDeltas.pEarDeer.pz2"\n'
        'readScript ":Runtime:libraries:pose:InjDeltas.EarDeer.pz2"\n'
        'readScript ":Runtime:libraries:pose:InjDeltas.Other.pz2"\n'
        'readScript ":Runtime:libraries:pose:Missing.pz2"\n}\n')

    result = scan_injection_package(str(orchestrator))
    assert [m.internal_name for m in result["morphs"]] == ["PBMDC_01", "PBMDC_02"]
    ear = result["morphs"][0]
    assert (ear.display_name, ear.delta_count) == ("EarDeer", 11)
    assert [os.path.basename(p) for p in ear.leaf_paths] == ["InjDeltas.pEarDeer.pz2", "InjDeltas.EarDeer.pz2"]
    assert result["unresolved_paths"] == [":Runtime:libraries:pose:Missing.pz2"]

    # And it agrees with the full parse about what the morphs are called.
    index = build_channel_index(load_injection_package(str(orchestrator))["figure"])
    assert {m.internal_name: m.display_name for m in result["morphs"]} == \
        {k: display_name_for(index, k) for k in index["by_internal"]}


def test_a_morph_with_no_label_anywhere_is_named_by_its_internal_name(tmp_path):
    leaf = _runtime(tmp_path) / "InjDeltas.X.pz2"
    leaf.write_text(_file(_actor("head:1", _channel("PBMDC_09", deltas=1))))
    assert scan_injection_package(str(leaf))["morphs"][0].display_name == "PBMDC_09"


@pytest.mark.skipif(not os.path.isdir(_REAL_CONTENT_ROOT), reason="Poser content drive not mounted")
@pytest.mark.parametrize("package", ["!V3 All Morphs INJ", "!M3 All Morphs INJ", "!V3 All Morphs REM"])
def test_scan_matches_a_full_parse_of_a_real_259_morph_package(package):
    path = os.path.join(_REAL_CONTENT_ROOT, "Base Figures", "Runtime", "libraries", "pose",
                        package, "! All Head Morphs.pz2")
    scanned = scan_injection_package(path)
    full = load_injection_package(path)
    index = build_channel_index(full["figure"])

    assert scanned["unresolved_paths"] == full["unresolved_paths"] == []
    assert scanned["leaf_paths"] == full["leaf_paths"]
    assert {m.internal_name: m.display_name for m in scanned["morphs"]} == \
        {k: display_name_for(index, k) for k in index["by_internal"]}
    # Parsing only a morph's own files yields that morph (what the picker relies on).
    morph = next(m for m in scanned["morphs"] if len(m.leaf_paths) > 1)
    partial = load_injection_package(path, only_leaves=morph.leaf_paths)
    assert list(build_channel_index(partial["figure"])["by_internal"]) == [morph.internal_name]
    assert partial["leaf_paths"] == morph.leaf_paths


def _morph(internal, name=None, deltas=5, binary=False):
    from core.cr2.injection_scan import ScannedMorph
    return ScannedMorph(internal_name=internal, display_name=name or internal,
                        delta_count=deltas, uses_binary_morph=binary, leaf_paths=[])


def test_classify_morphs_assigns_one_reason_each_in_priority_order():
    from core.cr2.injection_scan import (
        STATUS_BINARY, STATUS_EMPTY, STATUS_JCM, STATUS_PRESENT, classify_morphs)
    morphs = [_morph("A"), _morph("B", "JCMrFoo"), _morph("C", "Have"), _morph("PBMD", "Old"),
              _morph("E", binary=True, deltas=0), _morph("F", deltas=0),
              _morph("G", "JCMhave", binary=True)]
    result = classify_morphs(morphs, ["Basis", "Have", "PBMD", "JCMhave"], lambda n: n.startswith("JCM"))
    assert [status for _m, status in result] == [
        "", STATUS_JCM, STATUS_PRESENT, STATUS_PRESENT, STATUS_BINARY, STATUS_EMPTY, STATUS_JCM]
    assert [m.internal_name for m, _s in result] == ["A", "B", "C", "PBMD", "E", "F", "G"]


def test_estimate_scales_with_deltas_morphs_and_vertices():
    from core.cr2.injection_scan import estimate_apply
    seconds, size = estimate_apply([_morph("A", deltas=1_000_000), _morph("B", deltas=0)], 72_712)
    assert seconds == pytest.approx(4.8 + 2 * 0.024)
    assert size == 2 * 72_712 * 12
    assert estimate_apply([], 72_712) == (0.0, 0)

