# SPDX-License-Identifier: GPL-3.0-or-later
"""Tests for core/cr2/injection_package.py."""

import os
import textwrap

import pytest

from core.cr2.injection_package import load_injection_package

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
