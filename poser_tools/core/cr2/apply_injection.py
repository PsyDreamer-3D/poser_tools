# SPDX-License-Identifier: GPL-3.0-or-later
"""
apply_injection.py
--------------------
Combines one morph's per-actor inline deltas (a channel group from
core/cr2/name_match.py) into a single new shape key's position array on an
already-imported, already-FBX-reordered mesh.

Composes:
- core/cr2/actor_vertex_index.py (Phase 2) -- per-actor local delta index ->
  OBJ global vertex index.
- core/cr2/mesh_correspondence.py (Phase 1) -- OBJ global vertex index ->
  Blender vertex index, plus the recovered similarity transform (a delta
  *vector* needs the same permutation/sign/scale a *position* does, since
  it's a linear map -- there's just no translation component to add).

Original code -- cr2_importer never needed this. It builds its own mesh
directly from the OBJ (basis_pos = OBJ vertex order, no correspondence step
at all); poser_tools instead has to place deltas onto a mesh Blender's FBX
importer already reordered out from under the OBJ's own indexing.

Two collision types surface here that cr2_importer never has to handle,
because it never reorders vertices:
- Actor-to-actor seam sharing: two actors in the same channel group can
  legitimately touch the same OBJ global vertex (a shared body-part
  boundary). Resolved the same way cr2_importer's own
  ShapeKeyImporter._collect_morphs() does: once one actor's deltas have
  claimed an OBJ vertex, a later actor in the same group can't re-touch it.
- OBJ-to-Blender many-to-one: Phase 1's own `duplicate_targets` -- multiple
  *different* OBJ vertices landing on one Blender vertex (found during
  Phase 1's real-data verification; not a seam-sharing artifact, a property
  of the FBX import itself). When two of a morph's touched OBJ vertices
  collide on one Blender vertex, the one Phase 1 matched more precisely
  (smaller `distance`) wins -- not an average, not last-write-wins.
"""

import numpy as np

from .actor_vertex_index import resolve_local_index


def build_shape_key_positions(channel_group, actor_obj_groups, basis_positions, correspondence):
    """Build one new shape key's full position array from one morph's deltas.

    Args:
        channel_group: [(Actor, Channel), ...] for one morph, e.g. from
            core.cr2.name_match.match_channel_group().
        actor_obj_groups: {geom_name: [obj_global_idx, ...]} from
            core.cr2.actor_vertex_index.load_obj_actor_vertex_groups(), one
            entry per actor's geometry group -- computed once per figure,
            not per morph.
        basis_positions: (n_blender, 3) float64 -- the mesh's own current
            Basis shape key positions, in Blender space. The returned array
            starts as a copy of this; only vertices this morph actually
            touches differ from it.
        correspondence: the dict returned by
            core.cr2.mesh_correspondence.build_vertex_correspondence() for
            this figure (also computed once per figure, not per morph).

    Returns:
        {
            "positions": (n_blender, 3) float64 -- new shape key co array
            "touched_count": int -- vertices actually displaced
            "unmapped_count": int -- deltas that had no usable actor group,
                no resolvable local index, or landed on an unmatched
                (Phase 1 matched_index == -1) OBJ vertex; reported, not
                silently dropped
            "pmd_deltas_skipped": int -- channels with real deltas that exist
                only in an external .pmd (useBinaryMorph 1, Phase 4 territory)
                -- distinct from "no deltas at all", so a caller knows this
                morph is incomplete rather than genuinely empty
            "collisions_resolved": int -- OBJ-to-Blender many-to-one hits;
                the closer (smaller Phase 1 distance) contender won each time
            "seam_collisions_skipped": int -- deltas skipped because an
                earlier actor in this same group already claimed that OBJ
                vertex (a shared body-part boundary) -- the vertex is still
                touched (by the actor that claimed it first), just not
                double-counted; reported for the same reason
                collisions_resolved is, not because anything was lost
        }
    """
    positions = np.array(basis_positions, dtype=np.float64, copy=True)
    matched_index = correspondence["matched_index"]
    distance = correspondence["distance"]
    transform = correspondence["transform"]
    permutation = list(transform["permutation"])
    signs = np.array(transform["signs"], dtype=np.float64)
    scale = transform["scale"]

    unmapped_count = 0
    pmd_deltas_skipped = 0
    seam_collisions_skipped = 0
    claimed_obj_indices = set()

    # (blender_idx, delta_vector, obj_distance) per surviving contribution --
    # collected first, resolved second, so an OBJ-to-Blender collision never
    # needs to "undo" an already-applied delta (see module docstring).
    contributions = []

    for actor, channel in channel_group:
        if not channel.deltas:
            if channel.uses_binary_morph:
                pmd_deltas_skipped += 1
            continue

        group_name = actor.geom_name or actor.name
        obj_indices = actor_obj_groups.get(group_name)
        if obj_indices is None:
            unmapped_count += len(channel.deltas)
            continue

        for local_idx, dx, dy, dz in channel.deltas:
            global_idx = resolve_local_index(obj_indices, local_idx)
            if global_idx is None:
                unmapped_count += 1
                continue
            if global_idx in claimed_obj_indices:
                seam_collisions_skipped += 1
                continue
            claimed_obj_indices.add(global_idx)

            if global_idx >= len(matched_index):
                unmapped_count += 1
                continue
            blender_idx = int(matched_index[global_idx])
            if blender_idx < 0:
                unmapped_count += 1
                continue

            raw_delta = np.array([dx, dy, dz], dtype=np.float64)
            blender_delta = raw_delta[permutation] * signs * scale
            contributions.append((blender_idx, blender_delta, float(distance[global_idx])))

    touched_count = 0
    collisions_resolved = 0
    best_distance_by_blender_idx = {}
    best_delta_by_blender_idx = {}

    for blender_idx, blender_delta, obj_distance in contributions:
        prior = best_distance_by_blender_idx.get(blender_idx)
        if prior is None:
            best_distance_by_blender_idx[blender_idx] = obj_distance
            best_delta_by_blender_idx[blender_idx] = blender_delta
        else:
            collisions_resolved += 1
            if obj_distance < prior:
                best_distance_by_blender_idx[blender_idx] = obj_distance
                best_delta_by_blender_idx[blender_idx] = blender_delta
            # else: keep the existing (closer) winner.

    for blender_idx, blender_delta in best_delta_by_blender_idx.items():
        positions[blender_idx] += blender_delta
        touched_count += 1

    return {
        "positions": positions,
        "touched_count": touched_count,
        "unmapped_count": unmapped_count,
        "pmd_deltas_skipped": pmd_deltas_skipped,
        "collisions_resolved": collisions_resolved,
        "seam_collisions_skipped": seam_collisions_skipped,
    }
