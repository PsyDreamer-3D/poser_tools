# SPDX-License-Identifier: GPL-3.0-or-later
"""
Synthetic tests for core/cr2/mesh_correspondence.py -- no Test_Poser_Assets/
dependency, always run. Real-data verification (Aiko3, LaFemme) is a manual
headless-Blender step, not a pytest fixture -- see
docs/handoff-morph-injection.md Phase 1.
"""

import numpy as np
import pytest

from poser_tools.core.cr2.mesh_correspondence import build_vertex_correspondence


def _make_source_cloud(n=300, seed=0):
    rng = np.random.default_rng(seed)
    # Asymmetric per-axis scale so an axis permutation is actually
    # distinguishable from the identity -- a spherical cloud would score every
    # permutation identically.
    axis_scale = np.array([1.0, 2.0, 4.0])
    return rng.normal(size=(n, 3)) * axis_scale


def _apply_known_transform(points, perm, signs, scale, translation):
    return points[:, perm] * np.array(signs) * scale + translation


def test_round_trip_recovers_shuffled_correspondence():
    source = _make_source_cloud()
    perm = (2, 0, 1)
    signs = (1.0, -1.0, 1.0)
    scale = 3.5
    translation = np.array([10.0, -5.0, 2.0])

    target = _apply_known_transform(source, perm, signs, scale, translation)

    # Break index alignment (mirrors the real Aiko3/LaFemme situation) while
    # keeping ground truth known by construction.
    rng = np.random.default_rng(1)
    shuffle = rng.permutation(len(target))
    target_shuffled = target[shuffle]
    expected_match = np.empty_like(shuffle)
    expected_match[shuffle] = np.arange(len(shuffle))

    result = build_vertex_correspondence(source, target_shuffled, match_epsilon=1e-6)

    assert np.array_equal(result["matched_index"], expected_match)
    assert np.allclose(result["distance"], 0.0, atol=1e-6)
    assert result["transform"]["scale"] == pytest.approx(scale, rel=1e-3)
    assert not result["duplicate_targets"]


def test_duplicate_targets_reported():
    # Two source points (0 and its appended duplicate) with only one
    # corresponding target point between them -- e.g. a merge/weld on the
    # target side -- must both resolve to that one target index, and it must
    # show up in duplicate_targets.
    #
    # n needs to be large enough that one extra point barely perturbs the
    # coarse alignment's centroid/scale estimate (both are order-statistics
    # over the whole cloud, so a single unpaired point is a bigger relative
    # nudge on a small cloud than on a mesh-sized one) -- match_epsilon is
    # loosened accordingly, to the residual that small estimation noise
    # actually produces, not to bit-exact zero.
    unique_source = _make_source_cloud(n=3000, seed=3)
    source = np.vstack([unique_source, unique_source[0]])

    perm = (1, 2, 0)
    signs = (-1.0, 1.0, 1.0)
    scale = 2.0
    translation = np.array([1.0, 1.0, 1.0])
    target = _apply_known_transform(unique_source, perm, signs, scale, translation)

    result = build_vertex_correspondence(source, target, match_epsilon=0.05)

    assert result["matched_index"][0] == result["matched_index"][3000]
    tgt_idx = int(result["matched_index"][0])
    assert tgt_idx in result["duplicate_targets"]
    assert sorted(result["duplicate_targets"][tgt_idx]) == [0, 3000]


def test_unmatched_source_points_reported():
    # Extra source points with no corresponding target point at all (moved
    # far outside the target cloud) must come back unmatched, not
    # false-matched to whatever target point happens to be nearest. Same
    # cloud-size reasoning as the duplicate-targets test above re: n and
    # match_epsilon.
    base = _make_source_cloud(n=3000, seed=4)
    perm = (0, 1, 2)
    signs = (1.0, 1.0, 1.0)
    scale = 1.0
    translation = np.zeros(3)
    target = _apply_known_transform(base, perm, signs, scale, translation)

    extra = np.array(
        [
            [500.0, 500.0, 500.0],
            [-500.0, 300.0, 200.0],
            [400.0, -450.0, 350.0],
        ]
    )
    source = np.vstack([base, extra])

    result = build_vertex_correspondence(source, target, match_epsilon=0.05)

    assert np.array_equal(result["matched_index"][:3000], np.arange(3000))
    assert np.all(result["matched_index"][3000:] == -1)
