# SPDX-License-Identifier: GPL-3.0-or-later
"""Tests for core/cr2/injection_package.py."""

import os
import textwrap

import pytest

from core.cr2.injection_package import load_injection_package, paired_injection_path

_REAL_CONTENT_ROOT = (
    "/run/media/jess-green/236a91c9-2598-40dd-b968-9c0f0be8a903/Artwork/Poser Content"
)


def _write(path, text):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(textwrap.dedent(text))


def test_plain_leaf_file_parses_directly(tmp_path):
    leaf = tmp_path / "InjDeltas.Foo.pz2"
    _write(leaf, """\
        {
        version { number 4.01 }
        actor head:1
            {
            channels
                {
                targetGeom Foo
                    {
                    name Foo
                    deltas { d 0 0.1 0.2 0.3 }
                    }
                }
            }
        }
        """)
    result = load_injection_package(str(leaf))
    assert result["unresolved_paths"] == []
    actor_names = [a.name for a in result["figure"].actors]
    assert actor_names == ["head"]
    assert result["figure"].actors[0].channels[0].internal_name == "Foo"


def test_orchestrator_resolves_readscript_paths_whose_case_differs_from_disk(tmp_path):
    # Real packages write ":Runtime:Libraries:Pose:..." for a folder that is
    # "libraries/pose" on disk; a case-sensitive filesystem must still follow it.
    runtime = tmp_path / "MyLib" / "Runtime"
    orchestrator = runtime / "libraries" / "pose" / "Pack" / "! All.pz2"
    _write(orchestrator, """\
        {
        version { number 4.01 }
        readScript ":Runtime:Libraries:Pose:Leaves:INJDELTAS.FOO.PZ2"
        readScript ":Runtime:Libraries:Pose:Leaves:Missing.pz2"
        }
        """)
    _write(runtime / "libraries" / "pose" / "Leaves" / "InjDeltas.Foo.pz2", """\
        {
        version { number 4.01 }
        actor head:1
            {
            channels
                {
                targetGeom Foo
                    {
                    name Foo
                    deltas { d 0 0.1 0.2 0.3 }
                    }
                }
            }
        }
        """)

    result = load_injection_package(str(orchestrator))
    assert [a.name for a in result["figure"].actors] == ["head"]
    # Only the genuinely absent file is reported, with the path as written.
    assert result["unresolved_paths"] == [":Runtime:Libraries:Pose:Leaves:Missing.pz2"]


def test_orchestrator_follows_readscript_and_merges_actors(tmp_path):
    runtime = tmp_path / "MyLib" / "Runtime"
    pose_dir = runtime / "libraries" / "pose" / "Pack"
    deltas_dir = runtime / "libraries" / "!DAZ" / "Deltas"

    orchestrator = pose_dir / "! All Morphs.pz2"
    _write(orchestrator, """\
        {
        version { number 4.01 }
        readScript ":Runtime:libraries:!DAZ:Deltas:InjDeltas.Foo.pz2"
        readScript ":Runtime:libraries:!DAZ:Deltas:InjDeltas.Bar.pz2"
        }
        """)
    _write(deltas_dir / "InjDeltas.Foo.pz2", """\
        {
        version { number 4.01 }
        actor head:1
            {
            channels
                {
                targetGeom Foo
                    {
                    name Foo
                    deltas { d 0 0.1 0.2 0.3 }
                    }
                }
            }
        }
        """)
    _write(deltas_dir / "InjDeltas.Bar.pz2", """\
        {
        version { number 4.01 }
        actor chest:1
            {
            channels
                {
                targetGeom Bar
                    {
                    name Bar
                    deltas { d 0 0.4 0.5 0.6 }
                    }
                }
            }
        }
        """)

    result = load_injection_package(str(orchestrator))
    assert result["unresolved_paths"] == []
    actor_names = {a.name for a in result["figure"].actors}
    assert actor_names == {"head", "chest"}
    internal_names = {ch.internal_name for a in result["figure"].actors for ch in a.channels}
    assert internal_names == {"Foo", "Bar"}


def test_unresolvable_readscript_reported_not_silently_dropped(tmp_path):
    runtime = tmp_path / "MyLib" / "Runtime"
    orchestrator = runtime / "libraries" / "pose" / "! All Morphs.pz2"
    _write(orchestrator, """\
        {
        version { number 4.01 }
        readScript ":Runtime:libraries:!DAZ:Deltas:DoesNotExist.pz2"
        }
        """)

    result = load_injection_package(str(orchestrator))
    assert result["unresolved_paths"] == [":Runtime:libraries:!DAZ:Deltas:DoesNotExist.pz2"]
    assert result["figure"].actors == []


def test_orchestrator_referencing_orchestrator_recurses(tmp_path):
    runtime = tmp_path / "MyLib" / "Runtime"
    pose_dir = runtime / "libraries" / "pose" / "Pack"
    deltas_dir = runtime / "libraries" / "!DAZ" / "Deltas"

    top = pose_dir / "top.pz2"
    _write(top, """\
        {
        readScript ":Runtime:libraries:pose:Pack:middle.pz2"
        }
        """)
    middle = pose_dir / "middle.pz2"
    _write(middle, """\
        {
        readScript ":Runtime:libraries:!DAZ:Deltas:InjDeltas.Foo.pz2"
        }
        """)
    _write(deltas_dir / "InjDeltas.Foo.pz2", """\
        {
        actor head:1
            {
            channels
                {
                targetGeom Foo
                    {
                    deltas { d 0 0.1 0.2 0.3 }
                    }
                }
            }
        }
        """)

    result = load_injection_package(str(top))
    assert result["unresolved_paths"] == []
    assert [a.name for a in result["figure"].actors] == ["head"]


def test_same_file_referenced_twice_is_only_parsed_once(tmp_path):
    runtime = tmp_path / "MyLib" / "Runtime"
    pose_dir = runtime / "libraries" / "pose" / "Pack"
    deltas_dir = runtime / "libraries" / "!DAZ" / "Deltas"

    orchestrator = pose_dir / "! All Morphs.pz2"
    _write(orchestrator, """\
        {
        readScript ":Runtime:libraries:!DAZ:Deltas:InjDeltas.Foo.pz2"
        readScript ":Runtime:libraries:!DAZ:Deltas:InjDeltas.Foo.pz2"
        }
        """)
    _write(deltas_dir / "InjDeltas.Foo.pz2", """\
        {
        actor head:1
            {
            channels
                {
                targetGeom Foo
                    {
                    deltas { d 0 0.1 0.2 0.3 }
                    }
                }
            }
        }
        """)

    result = load_injection_package(str(orchestrator))
    assert [a.name for a in result["figure"].actors] == ["head"]


def test_real_orchestrator_package_resolves_and_merges():
    if not os.path.isdir(_REAL_CONTENT_ROOT):
        pytest.skip(f"Real Poser content mount not found at {_REAL_CONTENT_ROOT}")

    orchestrator = os.path.join(
        _REAL_CONTENT_ROOT,
        "Base Figures", "Runtime", "libraries", "pose", "!A3-H3 MORExp1 INJ",
        "! All Morphs.pz2",
    )
    if not os.path.isfile(orchestrator):
        pytest.skip("Real orchestrator file not found at expected path")

    result = load_injection_package(orchestrator)
    # Every readScript in this file (deltas + Unhide visibility toggles) must
    # resolve -- a real, shipped package with an unresolvable reference would
    # be a genuine bug worth failing on, not skipping past.
    assert result["unresolved_paths"] == []
    internal_names = {ch.internal_name for a in result["figure"].actors for ch in a.channels}
    assert "PBMDC_39" in internal_names  # BrowHeavy's internal name
    # BrowHeavy's own channel must have carried its inline deltas through.
    brow_heavy = next(
        ch for a in result["figure"].actors for ch in a.channels
        if ch.internal_name == "PBMDC_39"
    )
    assert len(brow_heavy.deltas) > 0


_LEAF = """\
    {
    version { number 4.01 }
    actor head:1
        {
        channels
            {
            targetGeom Foo
                {
                name Foo
                deltas { d 0 0.1 0.2 0.3 }
                }
            }
        }
    }
    """


def test_orchestrator_finds_readscript_files_in_another_configured_root(tmp_path):
    # The package lives in one library and names a file that exists only in another.
    lib_a, lib_b = tmp_path / "LibA", tmp_path / "LibB"
    orchestrator = lib_a / "Runtime" / "libraries" / "pose" / "Pack" / "! All.pz2"
    _write(orchestrator, """\
        {
        version { number 4.01 }
        readScript ":Runtime:libraries:!DAZ:Deltas:InjDeltas.Foo.pz2"
        }
        """)
    _write(lib_b / "Runtime" / "libraries" / "!DAZ" / "Deltas" / "InjDeltas.Foo.pz2", _LEAF)

    without = load_injection_package(str(orchestrator))
    assert without["unresolved_paths"] == [":Runtime:libraries:!DAZ:Deltas:InjDeltas.Foo.pz2"]
    assert without["figure"].actors == []

    found = load_injection_package(str(orchestrator), extra_roots=[str(lib_b)])
    assert found["unresolved_paths"] == []
    assert [a.name for a in found["figure"].actors] == ["head"]


def test_extra_roots_apply_through_nested_orchestrators_and_ignore_case(tmp_path):
    # A (entry) -> B (nested orchestrator, found in LibB) -> leaf found in LibC, with
    # the colon-paths' case differing from disk at every hop.
    lib_a, lib_b, lib_c = tmp_path / "LibA", tmp_path / "LibB", tmp_path / "LibC"
    entry = lib_a / "Runtime" / "libraries" / "pose" / "Entry.pz2"
    _write(entry, """\
        {
        version { number 4.01 }
        readScript ":Runtime:Libraries:!DAZ:Nested.pz2"
        }
        """)
    _write(lib_b / "Runtime" / "libraries" / "!DAZ" / "Nested.pz2", """\
        {
        version { number 4.01 }
        readScript ":RUNTIME:libraries:!daz:deltas:injdeltas.foo.pz2"
        }
        """)
    _write(lib_c / "Runtime" / "libraries" / "!DAZ" / "Deltas" / "InjDeltas.Foo.pz2", _LEAF)

    result = load_injection_package(str(entry), extra_roots=[str(lib_b), str(lib_c)])
    assert result["unresolved_paths"] == []
    assert [a.name for a in result["figure"].actors] == ["head"]


def test_real_package_resolves_files_from_another_configured_root():
    # "Custom Character Sets/.../!INJ Freja.pz2" names two files that live under the
    # separate "Base Figures" content root, so it only loads when that root is searched.
    if not os.path.isdir(_REAL_CONTENT_ROOT):
        pytest.skip(f"Real Poser content mount not found at {_REAL_CONTENT_ROOT}")
    package = os.path.join(
        _REAL_CONTENT_ROOT, "Custom Character Sets", "Runtime", "libraries", "Pose",
        "! Eldritch", "Freja", "!INJ Freja.pz2",
    )
    base_figures = os.path.join(_REAL_CONTENT_ROOT, "Base Figures")
    if not (os.path.isfile(package) and os.path.isdir(base_figures)):
        pytest.skip("Real package or Base Figures root not found at expected path")

    assert len(load_injection_package(package)["unresolved_paths"]) == 2

    result = load_injection_package(package, extra_roots=[base_figures])
    assert result["unresolved_paths"] == []
    assert len(result["figure"].actors) > 0


def test_leaf_paths_lists_every_parsed_file_but_not_the_orchestrator(tmp_path):
    runtime = tmp_path / "Runtime" / "libraries"
    runtime.mkdir(parents=True)
    leaf_text = "{\nactor head:1\n\t{\n\tchannels\n\t\t{\n\t\ttargetGeom X\n\t\t\t{\n\t\t\tname X\n\t\t\t}\n\t\t}\n\t}\n}\n"
    for name in ("a.pz2", "b.pz2"):
        (runtime / name).write_text(leaf_text)
    orchestrator = runtime / "! All.pz2"
    orchestrator.write_text('{\nreadScript ":Runtime:libraries:a.pz2"\nreadScript ":Runtime:libraries:b.pz2"\n}\n')

    result = load_injection_package(str(orchestrator))
    assert sorted(os.path.basename(p) for p in result["leaf_paths"]) == ["a.pz2", "b.pz2"]
    # A plain package is its own single leaf.
    single = str(runtime / "a.pz2")
    assert load_injection_package(single)["leaf_paths"] == [single]


def test_paired_injection_path_matches_by_file_name_ignoring_case(tmp_path):
    inj = tmp_path / "INJDELTAS.DC_42_EarFeline.pz2"
    inj.write_text("{}")
    rem = tmp_path / "RemDeltas.DC_42_EarFeline.pz2"
    rem.write_text("{}")
    assert paired_injection_path(str(rem)) == str(inj)


@pytest.mark.parametrize("name", ["InjDeltas.Foo.pz2", "Foo.pz2"])
def test_paired_injection_path_only_applies_to_removal_files(tmp_path, name):
    (tmp_path / name).write_text("{}")
    assert paired_injection_path(str(tmp_path / name)) is None


def test_paired_injection_path_is_none_when_there_is_no_pair(tmp_path):
    rem = tmp_path / "RemDeltas.Lonely.pz2"
    rem.write_text("{}")
    assert paired_injection_path(str(rem)) is None
