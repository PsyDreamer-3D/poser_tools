# SPDX-License-Identifier: GPL-3.0-or-later
"""Tests for core/cr2/apply_injection.py -- all synthetic, no Test_Poser_Assets/
dependency. Real-package verification (BrowHeavy, PBMMuscular) is a manual
headless-Blender step -- see docs/handoff-morph-injection.md Phase 3."""

import numpy as np

from core.cr2.apply_injection import build_shape_key_positions
from core.cr2.cr2_parser import Actor, Channel

_IDENTITY_TRANSFORM = {"permutation": (0, 1, 2), "signs": (1.0, 1.0, 1.0), "scale": 1.0}


def _correspondence(matched_index, distance, transform=None):
    return {
        "matched_index": np.array(matched_index, dtype=np.int64),
        "distance": np.array(distance, dtype=np.float64),
        "transform": transform or _IDENTITY_TRANSFORM,
        "duplicate_targets": {},
    }


def test_single_actor_delta_lands_on_correct_blender_vertex():
    # 4 OBJ verts -> "chest" group (sorted global indices [0, 1, 2, 3]); OBJ
    # and Blender indices coincide 1:1 here (identity correspondence).
    actor = Actor(name="chest")
    channel = Channel(internal_name="Foo", kind="targetGeom",
                       deltas=[(2, 1.0, 2.0, 3.0)])  # local idx 2 -> global idx 2
    basis = np.zeros((4, 3))
    correspondence = _correspondence([0, 1, 2, 3], [0.0, 0.0, 0.0, 0.0])

    result = build_shape_key_positions(
        [(actor, channel)],
        actor_obj_groups={"chest": [0, 1, 2, 3]},
        basis_positions=basis,
        correspondence=correspondence,
    )
    assert result["touched_count"] == 1
    assert result["unmapped_count"] == 0
    assert np.allclose(result["positions"][2], [1.0, 2.0, 3.0])
    assert np.allclose(result["positions"][0], [0.0, 0.0, 0.0])


def test_delta_vector_is_rotated_scaled_by_the_recovered_transform():
    # Non-identity transform: permute (0,2,1), signs (+,-,+), scale 2 --
    # matches the real transform found for Aiko3/LaFemme.
    transform = {"permutation": (0, 2, 1), "signs": (1.0, -1.0, 1.0), "scale": 2.0}
    actor = Actor(name="chest")
    channel = Channel(internal_name="Foo", kind="targetGeom",
                       deltas=[(0, 1.0, 2.0, 3.0)])
    basis = np.zeros((1, 3))
    correspondence = _correspondence([0], [0.0], transform=transform)

    result = build_shape_key_positions(
        [(actor, channel)],
        actor_obj_groups={"chest": [0]},
        basis_positions=basis,
        correspondence=correspondence,
    )
    # raw (1,2,3)[perm=(0,2,1)] = (1,3,2), * signs(+,-,+) = (1,-3,2), * scale 2 = (2,-6,4)
    assert np.allclose(result["positions"][0], [2.0, -6.0, 4.0])


def test_actor_uses_geom_name_over_name_when_present():
    actor = Actor(name="rCollar", geom_name="Collar_Right")
    channel = Channel(internal_name="Foo", kind="targetGeom", deltas=[(0, 1.0, 0.0, 0.0)])
    basis = np.zeros((1, 3))
    correspondence = _correspondence([0], [0.0])

    result = build_shape_key_positions(
        [(actor, channel)],
        actor_obj_groups={"Collar_Right": [0]},
        basis_positions=basis,
        correspondence=correspondence,
    )
    assert result["touched_count"] == 1
    assert result["unmapped_count"] == 0


def test_actor_to_actor_seam_collision_only_applies_first_claim():
    # Two adjacent actors both touch shared OBJ global vertex 5 (a seam) --
    # only the first actor processed should win; the second is skipped, not
    # double-added, mirroring cr2_importer's own "claimed" dedup.
    actor_a = Actor(name="hip")
    actor_b = Actor(name="chest")
    channel_a = Channel(internal_name="Foo", kind="targetGeom", deltas=[(0, 1.0, 0.0, 0.0)])
    channel_b = Channel(internal_name="Foo", kind="targetGeom", deltas=[(0, 100.0, 0.0, 0.0)])
    basis = np.zeros((6, 3))
    correspondence = _correspondence([0] * 6, [0.0] * 6)

    result = build_shape_key_positions(
        [(actor_a, channel_a), (actor_b, channel_b)],
        actor_obj_groups={"hip": [5], "chest": [5]},
        basis_positions=basis,
        correspondence=correspondence,
    )
    assert result["seam_collisions_skipped"] == 1
    assert result["touched_count"] == 1
    assert np.allclose(result["positions"][0], [1.0, 0.0, 0.0])  # actor_a's delta, not actor_b's


def test_obj_to_blender_collision_prefers_the_closer_correspondence_match():
    # Two different OBJ vertices (2 and 3) both map to Blender vertex 0, with
    # different Phase-1 match distances -- the closer one's delta must win.
    actor = Actor(name="chest")
    channel = Channel(internal_name="Foo", kind="targetGeom",
                       deltas=[(0, 1.0, 0.0, 0.0), (1, 100.0, 0.0, 0.0)])
    basis = np.zeros((1, 3))
    # obj global idx 2 (local 0) has distance 0.01; obj global idx 3 (local 1)
    # has distance 0.5 -- the first delta should win.
    correspondence = _correspondence(
        matched_index=[-1, -1, 0, 0],  # obj idx 2 and 3 both -> blender idx 0
        distance=[np.inf, np.inf, 0.01, 0.5],
    )

    result = build_shape_key_positions(
        [(actor, channel)],
        actor_obj_groups={"chest": [2, 3]},  # sorted -> local 0=global 2, local 1=global 3
        basis_positions=basis,
        correspondence=correspondence,
    )
    assert result["collisions_resolved"] == 1
    assert result["touched_count"] == 1
    assert np.allclose(result["positions"][0], [1.0, 0.0, 0.0])


def test_unmatched_correspondence_index_is_reported_not_applied():
    actor = Actor(name="chest")
    channel = Channel(internal_name="Foo", kind="targetGeom", deltas=[(0, 1.0, 0.0, 0.0)])
    basis = np.zeros((1, 3))
    correspondence = _correspondence(matched_index=[-1], distance=[np.inf])

    result = build_shape_key_positions(
        [(actor, channel)],
        actor_obj_groups={"chest": [0]},
        basis_positions=basis,
        correspondence=correspondence,
    )
    assert result["unmapped_count"] == 1
    assert result["touched_count"] == 0
    assert np.allclose(result["positions"][0], [0.0, 0.0, 0.0])


def test_local_index_out_of_range_is_reported_not_applied():
    actor = Actor(name="chest")
    channel = Channel(internal_name="Foo", kind="targetGeom",
                       deltas=[(99, 1.0, 0.0, 0.0)])  # only 1 vertex in the group
    basis = np.zeros((1, 3))
    correspondence = _correspondence(matched_index=[0], distance=[0.0])

    result = build_shape_key_positions(
        [(actor, channel)],
        actor_obj_groups={"chest": [0]},
        basis_positions=basis,
        correspondence=correspondence,
    )
    assert result["unmapped_count"] == 1
    assert result["touched_count"] == 0


def test_actor_with_no_matching_obj_group_reports_all_its_deltas_unmapped():
    actor = Actor(name="chest")
    channel = Channel(internal_name="Foo", kind="targetGeom",
                       deltas=[(0, 1.0, 0.0, 0.0), (1, 2.0, 0.0, 0.0)])
    basis = np.zeros((2, 3))
    correspondence = _correspondence(matched_index=[0, 1], distance=[0.0, 0.0])

    result = build_shape_key_positions(
        [(actor, channel)],
        actor_obj_groups={},  # "chest" never resolved to an OBJ group at all
        basis_positions=basis,
        correspondence=correspondence,
    )
    assert result["unmapped_count"] == 2
    assert result["touched_count"] == 0


def test_pmd_referenced_channel_is_counted_separately_from_empty_channel():
    actor = Actor(name="chest")
    pmd_channel = Channel(internal_name="Shldr_Gap_ADJ_R", kind="targetGeom",
                           deltas=[], uses_binary_morph=True)
    empty_channel = Channel(internal_name="Bar", kind="targetGeom", deltas=[])
    basis = np.zeros((1, 3))
    correspondence = _correspondence(matched_index=[0], distance=[0.0])

    result = build_shape_key_positions(
        [(actor, pmd_channel), (actor, empty_channel)],
        actor_obj_groups={"chest": [0]},
        basis_positions=basis,
        correspondence=correspondence,
    )
    assert result["pmd_deltas_skipped"] == 1
    assert result["touched_count"] == 0
    assert result["unmapped_count"] == 0


def test_returned_positions_is_a_copy_not_a_view_of_basis():
    basis = np.zeros((1, 3))
    correspondence = _correspondence(matched_index=[0], distance=[0.0])
    result = build_shape_key_positions(
        [], actor_obj_groups={}, basis_positions=basis, correspondence=correspondence
    )
    result["positions"][0, 0] = 999.0
    assert basis[0, 0] == 0.0
