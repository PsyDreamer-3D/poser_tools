# SPDX-License-Identifier: GPL-3.0-or-later
"""
correspondence_cache.py
-----------------------
Serializes a build_vertex_correspondence() result so it can be reused instead
of rebuilt. The build is ~34s on a 72k-vertex figure and depends only on the
reference OBJ's vertices and the mesh's basis positions, so a repeat run on an
unchanged mesh can skip it.

Pure numpy, no `bpy` (the operator decides where the blob lives). Reuse is
keyed on the exact input bytes, not a spot check: an edited mesh or a
different reference OBJ yields a different key, so a stale map can't be
applied silently.
"""

import hashlib
import io

import numpy as np

from .mesh_correspondence import find_duplicate_targets

# Bump when build_vertex_correspondence()'s results or this blob format change,
# so older cached blobs are rebuilt instead of trusted.
CACHE_VERSION = 1


def correspondence_key(source_points, target_points, match_epsilon=1e-3) -> str:
    """Hex digest identifying one build_vertex_correspondence() call's inputs."""
    source = np.ascontiguousarray(source_points, dtype=np.float64)
    target = np.ascontiguousarray(target_points, dtype=np.float64)
    digest = hashlib.sha256()
    digest.update(f"v{CACHE_VERSION}|{match_epsilon!r}|{source.shape}|{target.shape}".encode())
    digest.update(source.tobytes())
    digest.update(target.tobytes())
    return digest.hexdigest()


def pack_correspondence(result: dict) -> bytes:
    """Compress the parts of a correspondence the morph code reads.

    `duplicate_targets` is not stored: it's derived from `matched_index`.
    """
    matched_index = np.asarray(result["matched_index"])
    if len(matched_index) >= 2**31:
        raise ValueError("correspondence too large to cache")
    transform = result["transform"]
    buffer = io.BytesIO()
    np.savez_compressed(
        buffer,
        version=np.array([CACHE_VERSION]),
        matched_index=matched_index.astype(np.int32),
        distance=np.asarray(result["distance"], dtype=np.float64),
        permutation=np.array(transform["permutation"], dtype=np.int8),
        signs=np.array(transform["signs"], dtype=np.int8),
        scale=np.array([transform["scale"]], dtype=np.float64),
    )
    return buffer.getvalue()


def unpack_correspondence(blob: bytes, n_source: int, n_target: int) -> dict:
    """Rebuild the dict build_vertex_correspondence() returns.

    Raises ValueError if the blob is unreadable, from another format version,
    or inconsistent with the given sizes. `matched_index` is later used to
    index mesh arrays, so out-of-range values are rejected here rather than
    trusted. The blob can come from a .blend file someone else made, so
    pickled data is never loaded.
    """
    try:
        with np.load(io.BytesIO(blob), allow_pickle=False) as data:
            version = int(data["version"][0])
            matched_index = data["matched_index"]
            distance = data["distance"]
            permutation = data["permutation"]
            signs = data["signs"]
            scale = float(data["scale"][0])
    except Exception as exc:  # corrupt zip, missing key, pickled object array, ...
        # Only the exception type goes in the message: numpy's own text for
        # non-npz bytes suggests loading with allow_pickle=True, which is the
        # one thing this function must never do (and not advice for a user).
        raise ValueError(f"cached correspondence is unreadable ({type(exc).__name__})") from exc

    if version != CACHE_VERSION:
        raise ValueError(f"cached correspondence is format v{version}, expected v{CACHE_VERSION}")
    if len(matched_index) != n_source or len(distance) != n_source:
        raise ValueError(
            f"cached correspondence covers {len(matched_index)} points, expected {n_source}"
        )
    if len(matched_index) and (matched_index.min() < -1 or matched_index.max() >= n_target):
        raise ValueError("cached correspondence refers to vertices outside the mesh")
    if len(permutation) != 3 or len(signs) != 3:
        raise ValueError("cached correspondence has a malformed transform")

    matched_index = matched_index.astype(np.int64)
    return {
        "matched_index": matched_index,
        "distance": distance,
        "transform": {
            "permutation": tuple(int(v) for v in permutation),
            "signs": tuple(int(v) for v in signs),
            "scale": scale,
        },
        "duplicate_targets": find_duplicate_targets(matched_index),
    }
