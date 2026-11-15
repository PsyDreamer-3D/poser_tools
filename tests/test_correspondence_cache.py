# SPDX-License-Identifier: GPL-3.0-or-later
"""Tests for core/cr2/correspondence_cache.py -- synthetic, no external assets."""

import io

import numpy as np
import pytest

from poser_tools.core.cr2 import correspondence_cache as cc
from poser_tools.core.cr2.mesh_correspondence import build_vertex_correspondence, find_duplicate_targets


def _cloud(n=400, seed=0):
    rng = np.random.default_rng(seed)
    return rng.normal(size=(n, 3)) * np.array([1.0, 2.0, 4.0])


@pytest.fixture(scope="module")
def pair():
    """(source, target, result): a known transform plus a shuffle, then the real build.

    Built once per module: the build is the slow part, and none of the tests mutate it.
    """
    n = 400
    source = _cloud(n)
    target = source[:, (2, 0, 1)] * np.array([1.0, -1.0, 1.0]) * 3.5 + np.array([10.0, -5.0, 2.0])
    target = target[np.random.default_rng(1).permutation(n)]
    return source, target, build_vertex_correspondence(source, target, match_epsilon=1e-6)


def _handmade(matched, distance=None):
    """A correspondence dict built directly, for cases where the build isn't the point."""
    matched = np.array(matched, dtype=np.int64)
    return {
        "matched_index": matched,
        "distance": np.linspace(0.0, 1e-4, len(matched)) if distance is None else distance,
        "transform": {"permutation": (2, 0, 1), "signs": (1, -1, 1), "scale": 2.62128},
        "duplicate_targets": find_duplicate_targets(matched),
    }


def test_key_is_stable_and_ignores_memory_layout(pair):
    source, target, _ = pair
    key = cc.correspondence_key(source, target, 1e-6)
    assert key == cc.correspondence_key(source.copy(), target.copy(), 1e-6)
    # foreach_get hands back whatever layout it likes; identical values must key identically.
    assert key == cc.correspondence_key(np.asfortranarray(source), np.asfortranarray(target), 1e-6)


@pytest.mark.parametrize("change", ["source", "target", "epsilon", "shape"])
def test_key_changes_when_any_input_changes(pair, change):
    source, target, _ = pair
    base = cc.correspondence_key(source, target, 1e-6)
    if change == "source":
        source = source.copy(); source[17, 1] += 1e-9
    elif change == "target":
        target = target.copy(); target[3, 0] += 1e-9
    elif change == "epsilon":
        assert cc.correspondence_key(source, target, 1e-3) != base
        return
    elif change == "shape":
        target = target[:-1]
    assert cc.correspondence_key(source, target, 1e-6) != base


def test_round_trip_reproduces_the_fresh_result_exactly(pair):
    source, target, fresh = pair
    blob = cc.pack_correspondence(fresh)
    cached = cc.unpack_correspondence(blob, len(source), len(target))

    assert np.array_equal(cached["matched_index"], fresh["matched_index"])
    assert cached["matched_index"].dtype == fresh["matched_index"].dtype
    assert np.array_equal(cached["distance"], fresh["distance"])  # exact: collision ties compare these
    assert cached["transform"] == fresh["transform"]
    assert cached["duplicate_targets"] == fresh["duplicate_targets"]


def test_round_trip_keeps_unmatched_points_and_duplicates():
    fresh = _handmade([4, 1, 1, -1, 0, 2, 2, 2])
    assert fresh["duplicate_targets"] == {1: [1, 2], 2: [5, 6, 7]}

    cached = cc.unpack_correspondence(cc.pack_correspondence(fresh), 8, 5)
    assert np.array_equal(cached["matched_index"], fresh["matched_index"])
    assert cached["duplicate_targets"] == fresh["duplicate_targets"]
    assert cached["transform"] == fresh["transform"]


def test_blob_is_smaller_than_the_raw_arrays(pair):
    _, _, fresh = pair
    assert len(cc.pack_correspondence(fresh)) < fresh["matched_index"].nbytes + fresh["distance"].nbytes


@pytest.mark.parametrize("blob", [b"", b"not an npz at all", b"PK\x03\x04" + b"\x00" * 40])
def test_unreadable_blob_raises_value_error(blob):
    with pytest.raises(ValueError, match="unreadable"):
        cc.unpack_correspondence(blob, 10, 10)


def test_wrong_format_version_is_rejected(monkeypatch):
    blob = cc.pack_correspondence(_handmade([0, 1, 2]))
    monkeypatch.setattr(cc, "CACHE_VERSION", cc.CACHE_VERSION + 1)
    with pytest.raises(ValueError, match="format v"):
        cc.unpack_correspondence(blob, 3, 3)


def test_blob_for_a_different_size_is_rejected():
    blob = cc.pack_correspondence(_handmade([0, 1, 2]))
    with pytest.raises(ValueError, match="covers 3 points"):
        cc.unpack_correspondence(blob, 4, 3)


def test_out_of_range_vertex_index_is_rejected():
    # matched_index later indexes mesh arrays, so a tampered blob must not get through.
    blob = cc.pack_correspondence(_handmade([0, 1, 5]))   # 5 is not a vertex of a 3-vertex mesh
    with pytest.raises(ValueError, match="outside the mesh"):
        cc.unpack_correspondence(blob, 3, 3)


def test_pickled_object_arrays_are_never_loaded():
    # The blob can arrive inside a .blend from anyone; unpickling it could run code.
    buffer = io.BytesIO()
    np.savez_compressed(buffer, version=np.array([cc.CACHE_VERSION]),
                        matched_index=np.array([object()], dtype=object))
    with pytest.raises(ValueError, match="unreadable"):
        cc.unpack_correspondence(buffer.getvalue(), 1, 1)
