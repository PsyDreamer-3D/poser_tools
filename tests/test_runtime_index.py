# SPDX-License-Identifier: MIT
"""Tests for core/cr2/runtime_index.py.

get_cache_dir() needs bpy, so these tests monkeypatch load_cache/save_cache
to a tmp_path-backed pair instead of touching real Blender config -- the
scan/incremental-diff logic under test doesn't care where the cache lives.
"""

import os

import pytest

from core.cr2 import runtime_index as ri


@pytest.fixture(autouse=True)
def _isolated_cache(tmp_path, monkeypatch):
    """Redirect the JSON cache to a per-test tmp directory instead of the
    real Blender config dir (which needs bpy)."""
    cache_dir = tmp_path / "_cache"
    cache_dir.mkdir()
    monkeypatch.setattr(ri, "get_cache_dir", lambda: str(cache_dir))
    yield


def _make_runtime(base, *rel_files):
    """Create base/Runtime/Libraries/<rel_file> for each rel_file, returning
    base as the root."""
    for rel in rel_files:
        full = base / "Runtime" / "Libraries" / rel
        full.parent.mkdir(parents=True, exist_ok=True)
        full.write_text("{}")
    return str(base)


def test_index_runtime_finds_files_under_vendor_prefixed_folders(tmp_path):
    root = _make_runtime(
        tmp_path,
        "!DAZ/A3-H3MorExp1/Deltas/InjDeltas.DC_39_BrowHeavy.pz2",
        "Character/!Aiko 3.cr2",
    )
    items = ri.index_runtime(root)
    names = {i.name for i in items}
    assert "InjDeltas.DC_39_BrowHeavy" in names
    assert "!Aiko 3" in names


def test_index_runtime_ignores_unsupported_extensions(tmp_path):
    root = _make_runtime(tmp_path, "Pose/some_texture.jpg", "Pose/readme.txt")
    assert ri.index_runtime(root) == []


def test_index_runtime_returns_empty_list_when_no_runtime_folder(tmp_path):
    (tmp_path / "NotARuntime").mkdir()
    assert ri.index_runtime(str(tmp_path)) == []


def test_index_runtime_is_case_insensitive_on_runtime_and_libraries(tmp_path):
    full = tmp_path / "RUNTIME" / "libraries" / "Pose" / "file.pz2"
    full.parent.mkdir(parents=True)
    full.write_text("{}")
    items = ri.index_runtime(str(tmp_path))
    assert len(items) == 1
    assert items[0].name == "file"


def test_index_runtime_incremental_reuses_unchanged_entries(tmp_path):
    root = _make_runtime(tmp_path, "Pose/file.pz2")
    first = ri.index_runtime(root)
    assert len(first) == 1

    # Second call with nothing changed on disk should reuse the cached entry.
    second = ri.index_runtime(root)
    assert second == first


def test_index_runtime_incremental_rebuilds_changed_file(tmp_path):
    root = _make_runtime(tmp_path, "Pose/file.pz2")
    ri.index_runtime(root)

    target = tmp_path / "Runtime" / "Libraries" / "Pose" / "file.pz2"
    new_mtime = os.path.getmtime(target) + 5
    os.utime(target, (new_mtime, new_mtime))

    second = ri.index_runtime(root)
    assert len(second) == 1
    assert second[0].mtime == pytest.approx(new_mtime)


def test_index_runtime_incremental_drops_deleted_file(tmp_path):
    root = _make_runtime(tmp_path, "Pose/file.pz2", "Pose/other.cr2")
    first = ri.index_runtime(root)
    assert len(first) == 2

    os.remove(tmp_path / "Runtime" / "Libraries" / "Pose" / "file.pz2")
    second = ri.index_runtime(root)
    assert len(second) == 1
    assert second[0].name == "other"


def test_index_runtime_force_rescans_even_with_valid_cache(tmp_path):
    root = _make_runtime(tmp_path, "Pose/file.pz2")
    ri.index_runtime(root)
    forced = ri.index_runtime(root, force=True)
    assert len(forced) == 1


def test_cache_version_bump_invalidates_old_cache(tmp_path, monkeypatch):
    root = _make_runtime(tmp_path, "Pose/file.pz2")
    ri.index_runtime(root)

    monkeypatch.setattr(ri, "CACHE_VERSION", ri.CACHE_VERSION + 1)
    assert ri.load_cache(root) is None
    # Still works -- just re-scans from scratch.
    items = ri.index_runtime(root)
    assert len(items) == 1


def test_get_all_items_dedupes_across_roots_by_filepath(tmp_path):
    # Same root registered twice (e.g. a duplicate preferences entry) should
    # still surface each real file only once.
    root_a = _make_runtime(tmp_path / "shared_content", "Pose/shared.pz2")
    root_b = _make_runtime(tmp_path / "root_b", "Pose/only_in_b.pz2")

    items = ri.get_all_items([root_a, root_a, str(root_b)])
    names = sorted(i.name for i in items)
    assert names == ["only_in_b", "shared"]
