# SPDX-License-Identifier: MIT
"""Tests for core/cr2/poser_paths.py."""

import os
import re

import pytest

from core.cr2.poser_paths import find_runtime_root, resolve_poser_path

# Real Poser content library mount -- machine-specific, not always present.
# Not part of Test_Poser_Assets/ (large content-library tree); real-package
# tests skip cleanly when it isn't mounted, per docs/handoff-morph-injection.md.
_REAL_CONTENT_ROOT = (
    "/run/media/jess-green/236a91c9-2598-40dd-b968-9c0f0be8a903/Artwork/Poser Content"
)


def test_find_runtime_root_walks_up_to_nearest_runtime_ancestor(tmp_path):
    runtime = tmp_path / "MyPoserLib" / "Runtime"
    ref_file = runtime / "libraries" / "pose" / "SomePack" / "file.pz2"
    ref_file.parent.mkdir(parents=True)
    ref_file.write_text("{}")

    root = find_runtime_root(str(ref_file))
    assert root == str(tmp_path / "MyPoserLib")


def test_find_runtime_root_returns_none_when_no_runtime_ancestor(tmp_path):
    ref_file = tmp_path / "some" / "other" / "file.pz2"
    ref_file.parent.mkdir(parents=True)
    ref_file.write_text("{}")

    assert find_runtime_root(str(ref_file)) is None


def test_find_runtime_root_is_case_insensitive(tmp_path):
    runtime = tmp_path / "Lib" / "RUNTIME"
    ref_file = runtime / "libraries" / "file.pz2"
    ref_file.parent.mkdir(parents=True)
    ref_file.write_text("{}")

    assert find_runtime_root(str(ref_file)) == str(tmp_path / "Lib")


def test_resolve_poser_path_finds_file_relative_to_runtime_root(tmp_path):
    root = tmp_path / "MyPoserLib"
    ref_file = root / "Runtime" / "libraries" / "pose" / "Pack" / "orchestrator.pz2"
    ref_file.parent.mkdir(parents=True)
    ref_file.write_text("{}")

    target = root / "Runtime" / "libraries" / "!DAZ" / "Deltas" / "InjDeltas.Foo.pz2"
    target.parent.mkdir(parents=True)
    target.write_text("{}")

    resolved = resolve_poser_path(
        ":Runtime:libraries:!DAZ:Deltas:InjDeltas.Foo.pz2", str(ref_file)
    )
    assert resolved == str(target.resolve())


def test_resolve_poser_path_returns_none_when_file_does_not_exist(tmp_path):
    root = tmp_path / "MyPoserLib"
    ref_file = root / "Runtime" / "libraries" / "pose" / "orchestrator.pz2"
    ref_file.parent.mkdir(parents=True)
    ref_file.write_text("{}")

    assert resolve_poser_path(":Runtime:libraries:Nope.pz2", str(ref_file)) is None


def test_resolve_poser_path_tries_extra_roots(tmp_path):
    # No Runtime ancestor at all above ref_file -- only an extra_root works.
    ref_file = tmp_path / "elsewhere" / "orchestrator.pz2"
    ref_file.parent.mkdir(parents=True)
    ref_file.write_text("{}")

    extra_root = tmp_path / "SecondPoserLib"
    target = extra_root / "Runtime" / "libraries" / "Foo.pz2"
    target.parent.mkdir(parents=True)
    target.write_text("{}")

    resolved = resolve_poser_path(
        ":Runtime:libraries:Foo.pz2", str(ref_file), extra_roots=[str(extra_root)]
    )
    assert resolved == str(target.resolve())


def _case_sensitive_fs(tmp_path) -> bool:
    (tmp_path / "probe_a").write_text("")
    return not (tmp_path / "PROBE_A").exists()


def test_resolve_poser_path_matches_ignoring_case_and_returns_on_disk_casing(tmp_path):
    root = tmp_path / "MyPoserLib"
    ref_file = root / "Runtime" / "libraries" / "pose" / "Pack" / "orchestrator.pz2"
    ref_file.parent.mkdir(parents=True)
    ref_file.write_text("{}")

    target = root / "Runtime" / "libraries" / "!DAZ" / "Deltas" / "InjDeltas.Foo.pz2"
    target.parent.mkdir(parents=True)
    target.write_text("{}")

    # Colon-path casing differs from disk in a folder and in the file name.
    resolved = resolve_poser_path(
        ":Runtime:Libraries:!DAZ:DELTAS:injdeltas.foo.pz2", str(ref_file)
    )
    assert resolved is not None
    assert os.path.samefile(resolved, target)
    assert resolved.endswith(os.path.join("libraries", "!DAZ", "Deltas", "InjDeltas.Foo.pz2"))


def test_resolve_poser_path_prefers_exact_case_over_a_case_variant_sibling(tmp_path):
    if not _case_sensitive_fs(tmp_path):
        pytest.skip("filesystem is case-insensitive; can't hold both spellings")
    root = tmp_path / "Lib"
    ref_file = root / "Runtime" / "libraries" / "ref.pz2"
    ref_file.parent.mkdir(parents=True)
    ref_file.write_text("{}")
    exact = root / "Runtime" / "libraries" / "Foo.pz2"
    variant = root / "Runtime" / "libraries" / "foo.pz2"
    exact.write_text("exact")
    variant.write_text("variant")

    assert resolve_poser_path(":Runtime:libraries:Foo.pz2", str(ref_file)) == str(exact)
    # No exact spelling: deterministic pick (sorted), not filesystem order.
    assert resolve_poser_path(":Runtime:libraries:FOO.pz2", str(ref_file)) == str(exact)


def test_resolve_poser_path_ignoring_case_never_returns_a_directory(tmp_path):
    root = tmp_path / "Lib"
    ref_file = root / "Runtime" / "libraries" / "ref.pz2"
    ref_file.parent.mkdir(parents=True)
    ref_file.write_text("{}")
    (root / "Runtime" / "libraries" / "SomeFolder").mkdir()

    assert resolve_poser_path(":Runtime:LIBRARIES:somefolder", str(ref_file)) is None


def test_resolve_poser_path_ignoring_case_in_extra_roots(tmp_path):
    ref_file = tmp_path / "elsewhere" / "orchestrator.pz2"
    ref_file.parent.mkdir(parents=True)
    ref_file.write_text("{}")

    extra_root = tmp_path / "SecondPoserLib"
    target = extra_root / "Runtime" / "libraries" / "Foo.pz2"
    target.parent.mkdir(parents=True)
    target.write_text("{}")

    resolved = resolve_poser_path(
        ":RUNTIME:Libraries:foo.PZ2", str(ref_file), extra_roots=[str(extra_root)]
    )
    assert resolved is not None
    assert os.path.samefile(resolved, target)


def test_resolve_poser_path_dir_cache_avoids_relisting_directories(tmp_path, monkeypatch):
    root = tmp_path / "Lib"
    ref_file = root / "Runtime" / "libraries" / "ref.pz2"
    ref_file.parent.mkdir(parents=True)
    ref_file.write_text("{}")
    deltas = root / "Runtime" / "libraries" / "Deltas"
    deltas.mkdir()
    for i in range(5):
        (deltas / f"f{i}.pz2").write_text("{}")

    calls = []
    real_listdir = os.listdir
    monkeypatch.setattr(os, "listdir", lambda d: (calls.append(d), real_listdir(d))[1])

    def resolve_all(**kw):
        calls.clear()
        for i in range(5):  # wrong case forces the case-insensitive path
            assert resolve_poser_path(f":Runtime:LIBRARIES:deltas:F{i}.pz2", str(ref_file), **kw)
        return len(calls)

    uncached = resolve_all()
    cache = {}
    cached = resolve_all(dir_cache=cache)

    assert cached < uncached
    assert cached == len(cache)  # each distinct directory listed exactly once


def test_resolve_poser_path_against_real_injection_package():
    if not os.path.isdir(_REAL_CONTENT_ROOT):
        pytest.skip(f"Real Poser content mount not found at {_REAL_CONTENT_ROOT}")

    orchestrator = os.path.join(
        _REAL_CONTENT_ROOT,
        "Base Figures", "Runtime", "libraries", "pose", "!A3-H3 MORExp1 INJ",
        "! All Morphs.pz2",
    )
    if not os.path.isfile(orchestrator):
        pytest.skip("Real orchestrator file not found at expected path")

    resolved = resolve_poser_path(
        ":Runtime:libraries:!DAZ:A3-H3MorExp1:Deltas:InjDeltas.DC_39_BrowHeavy.pz2",
        orchestrator,
    )
    assert resolved is not None
    assert os.path.isfile(resolved)


def test_resolve_poser_path_follows_real_orchestrator_with_mismatched_case():
    # "!V3 All Morphs INJ/! All Head Morphs.pz2" writes ":Runtime:Libraries:Pose:..."
    # for on-disk "libraries/pose": 256 of its 257 targets used to miss on a
    # case-sensitive filesystem, so loading it applied nothing.
    if not os.path.isdir(_REAL_CONTENT_ROOT):
        pytest.skip(f"Real Poser content mount not found at {_REAL_CONTENT_ROOT}")

    orchestrator = os.path.join(
        _REAL_CONTENT_ROOT,
        "Base Figures", "Runtime", "libraries", "pose", "!V3 All Morphs INJ",
        "! All Head Morphs.pz2",
    )
    if not os.path.isfile(orchestrator):
        pytest.skip("Real orchestrator file not found at expected path")

    with open(orchestrator, encoding="utf-8", errors="replace") as fh:
        targets = re.findall(r'readScript\s+"([^"]+)"', fh.read())
    assert len(targets) > 200

    cache = {}
    unresolved = [
        t for t in targets
        if resolve_poser_path(t, orchestrator, dir_cache=cache) is None
    ]
    assert unresolved == []
