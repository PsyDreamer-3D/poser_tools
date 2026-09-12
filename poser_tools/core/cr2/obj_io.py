# SPDX-License-Identifier: GPL-3.0-or-later
"""
obj_io.py
---------
Minimal Wavefront .obj reading for cross-referencing Poser's native geometry
against an FBX-imported Blender mesh.

Original poser_tools code (unlike its MIT-headed siblings in this package,
which are adapted from cr2_importer) -- there's no equivalent in cr2_importer
that fits this narrow a job.

Only reads vertex positions, in file order. No face/group parsing here --
that's per-actor index resolution, a separate later piece
(docs/handoff-morph-injection.md Phase 2), not needed for vertex
correspondence alone.
"""

import numpy as np


def load_obj_vertex_positions(path: str) -> np.ndarray:
    """Parse every `v x y z` line from a Wavefront .obj, in file order.

    Ignores everything else (faces, groups, normals, UVs, comments). Returns
    an (N, 3) float64 array; row i is the i'th "v" line's position -- i.e.
    the OBJ's own vertex index, 0-based (OBJ files are 1-based in face refs,
    but that indexing doesn't matter here since we only read positions).
    """
    positions = []
    with open(path, "r", encoding="utf-8", errors="replace") as fh:
        for line in fh:
            if not line.startswith("v "):
                continue
            parts = line.split()
            # "v x y z" or "v x y z w" (homogeneous w rarely used, ignored) or
            # "v x y z r g b" (per-vertex color, some Poser-era exporters) --
            # only the first three numbers after "v" are the position.
            positions.append((float(parts[1]), float(parts[2]), float(parts[3])))
    return np.array(positions, dtype=np.float64)
