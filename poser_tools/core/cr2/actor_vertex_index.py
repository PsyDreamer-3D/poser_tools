# SPDX-License-Identifier: MIT
"""
actor_vertex_index.py
----------------------
Resolves a Poser targetGeom delta's per-actor local vertex index to the
reference .obj's global vertex index.

Adapted from cr2_importer's core/obj_loader.py (group/face tracking) and
core/shape_key_importer.py (the local -> global index convention itself:
`sorted_verts = sorted(set(group.vertex_indices()))`, then
`global_idx = sorted_verts[local_idx]`) -- confirmed against real, working
code rather than reverse-engineered from the CR2/OBJ grammar alone. See
docs/handoff-morph-injection.md Phase 2 for why this translation is needed:
a `d 47 dx dy dz` delta's "47" is local to one actor's own geometry, not the
OBJ file's global vertex numbering.

Deliberately not a full mesh/material/UV importer like cr2_importer's
obj_loader.py -- this only tracks what's needed to answer "which global
vertex indices does actor X's geometry touch, in Poser's local order":
`g <name>` group boundaries and each face's referenced vertex indices.
"""

from collections import defaultdict


def load_obj_actor_vertex_groups(path: str) -> dict:
    """Parse a Poser .obj file's `g <name>` groups into each group's globally
    unique vertex indices, sorted ascending.

    Poser assigns each actor's targetGeom deltas a vertex index local to that
    actor's own geometry -- not the OBJ's global vertex order. cr2_importer's
    shape_key_importer.py resolves this as `sorted(set(group.vertex_indices()))`:
    local index N is the N-th smallest unique global vertex index referenced
    by that actor's faces. This returns exactly that per group, so
    `result[group_name][local_idx]` is the global OBJ vertex index for a
    delta with that local index.

    A CR2 actor's OBJ group name is `actor.geom_name or actor.name`
    (`core/cr2/cr2_parser.py`'s `Actor` fields) -- this function only knows
    about OBJ group names, resolving that to a specific actor is the caller's
    job (composing with `cr2_parser.Figure`).

    Returns {group_name: [global_vertex_index, ...]}, 0-based, ascending.
    Faces before any `g` directive fall into a `"default"` group, matching
    cr2_importer's own convention.
    """
    groups = defaultdict(set)
    current_group = "default"
    vertex_count = 0

    with open(path, "r", encoding="utf-8", errors="replace") as fh:
        for raw_line in fh:
            line = raw_line.strip()
            if not line or line.startswith("#"):
                continue
            parts = line.split()
            directive = parts[0]

            if directive == "v":
                vertex_count += 1

            elif directive == "g":
                # Poser sometimes emits "g group1 group2"; cr2_importer takes
                # only the first name, so this does too.
                current_group = parts[1] if len(parts) > 1 else "default"

            elif directive == "f":
                for token in parts[1:]:
                    if not token:
                        continue
                    vi = int(token.split("/")[0])
                    # OBJ face refs are 1-based, and can be negative (relative
                    # to the vertex count *at this point in the file*, per the
                    # OBJ spec) -- real Poser exports observed so far only use
                    # positive refs, but resolve negative ones correctly
                    # rather than silently mis-indexing if one ever shows up.
                    global_idx = (vi - 1) if vi > 0 else (vertex_count + vi)
                    groups[current_group].add(global_idx)

    return {name: sorted(indices) for name, indices in groups.items()}


def resolve_local_index(group_vertex_indices: list, local_index: int):
    """Translate one actor-local vertex index (as used in a `d <local_idx> ...`
    delta) to the OBJ's global vertex index, given that actor's own sorted
    unique vertex-index list (one value from `load_obj_actor_vertex_groups`'s
    result).

    Returns None for an out-of-range local index rather than raising --
    callers report this, per the same "surface, don't silently drop"
    discipline as Phase 1's unmatched-vertex handling.
    """
    if 0 <= local_index < len(group_vertex_indices):
        return group_vertex_indices[local_index]
    return None
