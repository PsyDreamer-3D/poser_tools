# SPDX-License-Identifier: GPL-3.0-or-later
"""
mesh_correspondence.py
-----------------------
Matches a Poser-native point cloud (a figure's reference .obj, or a PZ2/PMD
delta's implicit local vertex order) against the same figure's vertex
positions after Blender has imported it via FBX.

Why this is needed at all: Poser's FBX export doesn't preserve vertex order
relative to the figure's own .obj (see docs/handoff-morph-injection.md), so a
morph-package delta's vertex index can't be applied directly to the
FBX-imported mesh. What *is* preserved is 3D position, up to one fixed
similarity transform (uniform scale + axis permutation/reflection, no
per-vertex distortion) -- confirmed empirically against two figures (Aiko3,
LaFemme) with the same recovered transform both times, matching
cr2_importer's own hardcoded Poser->Blender scale constant. This module
recovers that transform with no assumed index correspondence, then matches
points by position.

Pure NumPy, no bpy and no mathutils.kdtree -- every function here takes/
returns plain arrays so it's testable head-on with pytest and synthetic point
clouds, the same shape as cr2_parser.py/name_match.py in this package.

`_coarse_align`'s 48-candidate scoring stays on the original per-point search
(`_grid_nearest_neighbor`, below) -- real timing showed it was never the
bottleneck (~13s of a 283s run) and its own vectorized rewrite turned out to
have a real trap of its own (see docs/handoff-morph-injection.md Phase 5.2):
a non-uniform point cloud (real mesh or synthetic) can make a *wrong*
candidate's sample points spuriously "find" a dense region once the search
radius grows, and summing that across a batch of query points costs far more
than the same per-point loop paying for it individually. The two real
full-mesh passes in `build_vertex_correspondence` -- which *were* ~93% of
the 283s -- use the vectorized `_query_grid` instead, sized to a tight
distance tolerance per pass rather than a density guess, which is what
actually made them safe to vectorize.
"""

import itertools
from collections import defaultdict, namedtuple

import numpy as np

# Aim for roughly this many target points per grid cell -- few enough that a
# cell's occupant list stays cheap to scan, many enough that we don't spend
# most of the search expanding empty rings. Used by the (unchanged)
# per-point _grid_nearest_neighbor search below.
_TARGET_POINTS_PER_CELL = 4.0

# Cell-id encoding for _query_grid's vectorized nearest-neighbor search:
# each axis is offset into [0, _CELL_BASE) then packed into one int64 via
# positional encoding in that base. _CELL_BASE**3 must stay comfortably
# under int64's 2**63 ceiling -- (2**20)**3 == 2**60, leaving a healthy
# margin.
_CELL_BASE = 1 << 20
_CELL_OFFSET = _CELL_BASE // 2
# Floors how fine the *target* grid can get relative to the cloud's own
# extent. Without this, a very small cell_size (e.g. this module's own tests
# use match_epsilon as low as 1e-6) could push the target grid's own span
# past _CELL_BASE and into the encoding's "out of range" case.
_MAX_GRID_SPAN = 4096

_QUERY_CHUNK_SIZE = 8192

# Pass 1 (pre-scale-refit, in build_vertex_correspondence) needs a tolerance
# generous enough that essentially every genuine correspondence still counts
# as "trusted" for the least-squares scale refit below -- the old,
# unbounded-search pass 1 found *some* nearest neighbor for virtually every
# point (however good or bad), so in practice almost everything counted as
# trusted. This is a real tension with performance, not just a tuning knob:
# real Aiko3 data (measured directly, not guessed) has a genuinely dense
# anatomical region (~4,282 vertices inside one 2.7cm cube near the
# hip/pelvis) -- a *larger* tolerance directly means more candidates packed
# into that region's cells, and an earlier, more "generous" factor (0.05)
# made real full-mesh passes take minutes and gigabytes, not seconds.
# Measured on real Aiko3 data: 0.005 and 0.01 both land 100% of points
# within tolerance (i.e. neither shrinks the trusted set at all vs. an
# effectively-unbounded search), at 2.45s and 11.76s respectively for the
# whole pass; 0.01 is used as a safety margin over the tightest value that
# still worked, for figures whose coarse-alignment residual might be
# somewhat worse than Aiko3's.
_PASS1_TOLERANCE_FACTOR = 0.01
# Pass 2 (post-refit) only cares about match_epsilon-close points anyway --
# the match_epsilon cutoff below discards everything else regardless of what
# this search finds. A small safety margin over match_epsilon itself avoids
# any floating-point boundary edge case exactly at the cutoff.
_PASS2_TOLERANCE_MARGIN = 1.5

_TargetGrid = namedtuple(
    "_TargetGrid",
    ["target_points", "mins", "cell_size", "unique_ids", "starts", "counts", "sorted_target_idx"],
)


def build_vertex_correspondence(source_points, target_points, match_epsilon=1e-3):
    """Match each `source_points` row to its corresponding `target_points` row,
    without assuming index alignment.

    Returns a dict:
        matched_index     -- int64 array, len(source_points); -1 where no
                              match was found within match_epsilon
        distance          -- float64 array, len(source_points)
        transform         -- {"permutation": (i,j,k), "signs": (s,s,s),
                              "scale": float} the coarse alignment found
        duplicate_targets -- {target_index: [source_index, ...]} for any
                              target matched by more than one source point
                              (len > 1 only)
    """
    transform, source_centroid, target_centroid, target_radius = _coarse_align(source_points, target_points)
    perm = transform["permutation"]
    signs = np.array(transform["signs"])
    scale = transform["scale"]

    source_centered = source_points - source_centroid
    target_centered = target_points - target_centroid
    permuted_signed_source = source_centered[:, perm] * signs

    pass1_cell_size = max(_PASS1_TOLERANCE_FACTOR * target_radius, match_epsilon)
    grid = _build_target_grid(target_centered, pass1_cell_size)

    aligned_source = permuted_signed_source * scale
    matched_index, distance = _query_grid(grid, aligned_source, rings=1)

    # _coarse_align's scale is a global-statistics estimate (mean/RMS radius
    # ratio) -- accurate to roughly 1% on real meshes, which is good enough to
    # get an initial correspondence but not tight enough to land every real
    # match inside match_epsilon (a 1% scale error is a growing absolute
    # position error the farther a point sits from the centroid). Now that
    # pass 1 has given us actual matched pairs, refit scale directly from
    # them -- ordinary least squares on `target = scale' * permuted_source`,
    # which for a single free scalar is just scale' = sum(src . tgt) /
    # sum(src . src) -- and re-match once with the refined value. Real data
    # (Aiko3/LaFemme) needed this second pass to reach a near-zero residual;
    # skip it if pass 1 found too little to refit from.
    trusted = np.isfinite(distance)
    if np.count_nonzero(trusted) >= 10:
        src = permuted_signed_source[trusted]
        tgt = target_centered[matched_index[trusted]]
        denominator = np.sum(src * src)
        if denominator > 0:
            refined_scale = float(np.sum(src * tgt) / denominator)
            aligned_source = permuted_signed_source * refined_scale
            pass2_cell_size = max(_PASS2_TOLERANCE_MARGIN * match_epsilon, 1e-9)
            grid = _build_target_grid(target_centered, pass2_cell_size)
            matched_index, distance = _query_grid(grid, aligned_source, rings=1)
            scale = refined_scale
            transform = dict(transform, scale=scale)

    unmatched = distance > match_epsilon
    matched_index = matched_index.copy()
    matched_index[unmatched] = -1

    groups = defaultdict(list)
    for src_idx, tgt_idx in enumerate(matched_index):
        if tgt_idx >= 0:
            groups[int(tgt_idx)].append(src_idx)
    duplicate_targets = {k: v for k, v in groups.items() if len(v) > 1}

    return {
        "matched_index": matched_index,
        "distance": distance,
        "transform": transform,
        "duplicate_targets": duplicate_targets,
    }


def _coarse_align(source, target, sample_size=500, seed=0):
    """Find the (permutation, signs, scale) that best overlays `source` onto
    `target`, without assuming any index correspondence between them.

    Centroid and scale come from the mean / RMS radius of each cloud, *after*
    dropping gross outliers -- and real meshes (verified against Aiko3 and
    LaFemme) turned out to need "mean/RMS over nearly everything", not
    "median": a real body mesh's per-vertex radius about its own centroid is
    heavily right-skewed (most vertices sit close in, a long tail runs out
    to the fingertips/toes), so the median is exactly the wrong statistic --
    small changes in which points are even present (a handful of stray
    unmatched vertices on one side) shift which vertex happens to sit at the
    50th percentile, and because the distribution is steep there, that shifts
    the *value* a lot. Mean/RMS over the whole cloud barely notices the same
    handful of points. What mean/RMS genuinely can't tolerate is a
    *gross* outlier (a source vertex with nothing to inject onto, sitting far
    outside the cloud) -- one such point skews a mean/RMS badly, which is
    why this isn't just a plain mean/RMS. The fix is to only filter what
    mean/RMS actually can't handle: a per-point radius more than
    `_OUTLIER_MAD_MULTIPLIER` median-absolute-deviations from the median
    radius is dropped before the real (mean/RMS) statistics are computed.
    That multiplier is deliberately generous -- large enough that it never
    trims a single real mesh vertex (checked against both test figures) and
    only catches points that are wildly, unambiguously outside the cloud.
    """
    _OUTLIER_MAD_MULTIPLIER = 20.0

    def _mean_rms_excluding_gross_outliers(points):
        rough_centroid = np.median(points, axis=0)
        radius = np.linalg.norm(points - rough_centroid, axis=1)
        median_radius = np.median(radius)
        mad = np.median(np.abs(radius - median_radius))
        threshold = median_radius + _OUTLIER_MAD_MULTIPLIER * max(mad, 1e-9)
        inliers = points[radius <= threshold]
        centroid = inliers.mean(axis=0)
        rms_radius = np.sqrt(np.mean(np.sum((inliers - centroid) ** 2, axis=1)))
        return centroid, rms_radius

    source_centroid, source_radius = _mean_rms_excluding_gross_outliers(source)
    target_centroid, target_radius = _mean_rms_excluding_gross_outliers(target)
    source_centered = source - source_centroid
    target_centered = target - target_centroid

    scale = target_radius / source_radius

    rng = np.random.default_rng(seed)
    n_sample = min(sample_size, len(source_centered))
    sample_idx = rng.choice(len(source_centered), size=n_sample, replace=False)
    sample = source_centered[sample_idx]

    # A small, fixed ring cap here (see _grid_nearest_neighbor's docstring) --
    # under the right permutation every sample point should land within a
    # ring or two of its match, so this doesn't cost the correct candidate
    # anything; it's what keeps the other 47 (definitionally uncorrelated)
    # candidates from each paying for an exhaustive grid search just to be
    # ruled out.
    _SCORING_MAX_RADIUS = 4

    best_score = None
    best_perm = None
    best_signs = None
    for perm in itertools.permutations(range(3)):
        for signs in itertools.product((1.0, -1.0), repeat=3):
            candidate = sample[:, perm] * np.array(signs) * scale
            _, dist = _grid_nearest_neighbor(candidate, target_centered, max_radius=_SCORING_MAX_RADIUS)
            found = np.isfinite(dist)
            n_found = int(found.sum())
            mean_found_dist = float(dist[found].mean()) if n_found else float("inf")
            # Maximize points found within the cap, then minimize their mean
            # distance -- a wrong candidate typically finds almost nothing,
            # so n_found alone nearly always settles it.
            score = (-n_found, mean_found_dist)
            if best_score is None or score < best_score:
                best_score, best_perm, best_signs = score, perm, signs

    transform = {"permutation": best_perm, "signs": best_signs, "scale": float(scale)}
    return transform, source_centroid, target_centroid, target_radius


def _grid_nearest_neighbor(query_points, target_points, max_radius=None):
    """For each row in `query_points`, find its nearest row in `target_points`
    by bucketing `target_points` into a uniform grid and expanding the
    searched cell radius outward until a match is found (or the search
    exhausts the grid, meaning the query point has no plausible match).

    `max_radius` caps how many rings the search is allowed to expand through
    before giving up on a query point; left as None it defaults to the
    grid's own footprint (i.e. "search everywhere before giving up").
    `_coarse_align` passes a small explicit cap instead: under a *wrong*
    permutation/sign candidate, points don't correspond at all, so every one
    of its query points would otherwise expand all the way out to that
    full-footprint radius before giving up -- paying for an exhaustive
    search 47 times over just to prove 47 of the 48 candidates are wrong. A
    small cap makes a wrong candidate fail fast (most of its points find
    nothing within a few rings) while a correct candidate -- whose points
    sit right on top of their match -- is unaffected, since it never needs
    more than a ring or two anyway.

    This per-point loop is intentionally *not* the vectorized approach
    `_query_grid` below uses for the real full-mesh passes: `_coarse_align`
    only calls this over a small 500-point sample across 48 candidates
    (~13s of a real 283s run, never the bottleneck), and per-point handling
    sidesteps a real trap a vectorized rewrite of this exact call ran into
    (see docs/handoff-morph-injection.md Phase 5.2) -- a wrong candidate's
    points can spuriously cluster in a dense region of a non-uniform cloud,
    and summing that across a whole batch costs far more than paying for it
    per point individually.

    Returns (index, distance) int64/float64 arrays, len(query_points).
    `index` is -1 and `distance` is inf for a query point with no match.
    """
    n_query = len(query_points)
    n_target = len(target_points)
    if n_target == 0 or n_query == 0:
        return (
            np.full(n_query, -1, dtype=np.int64),
            np.full(n_query, np.inf, dtype=np.float64),
        )

    mins = target_points.min(axis=0)
    maxs = target_points.max(axis=0)
    extent = maxs - mins
    # A degenerate (flat/collinear) axis would otherwise divide by zero below.
    extent = np.where(extent <= 0, 1e-9, extent)
    volume = float(np.prod(extent))
    cell_size = max((volume * _TARGET_POINTS_PER_CELL / n_target) ** (1.0 / 3.0), 1e-9)

    grid = defaultdict(list)
    target_cells = np.floor((target_points - mins) / cell_size).astype(np.int64)
    for i, cell in enumerate(map(tuple, target_cells)):
        grid[cell].append(i)

    # Once the search radius has grown past the grid's own footprint, no
    # amount of further expansion can find anything -- treat as unmatched
    # rather than looping forever (this is what makes a query point far
    # outside the target cloud come back as -1).
    if max_radius is None:
        grid_span = np.ceil(extent / cell_size).astype(np.int64)
        max_radius = int(np.max(grid_span)) + 2

    query_cells = np.floor((query_points - mins) / cell_size).astype(np.int64)

    matched_index = np.full(n_query, -1, dtype=np.int64)
    distance = np.full(n_query, np.inf, dtype=np.float64)

    for qi in range(n_query):
        qcell = tuple(query_cells[qi])
        candidates = []
        radius = 1
        settled_radius = None
        while radius <= max_radius:
            candidates = [
                idx
                for dx in range(-radius, radius + 1)
                for dy in range(-radius, radius + 1)
                for dz in range(-radius, radius + 1)
                for idx in grid.get((qcell[0] + dx, qcell[1] + dy, qcell[2] + dz), ())
            ]
            if candidates:
                if settled_radius is None:
                    # A closer point could still be in a cell just outside this
                    # radius (grid cells are square, distance isn't), so widen
                    # one more ring once before accepting the candidate set.
                    settled_radius = radius
                    radius += 1
                    continue
                break
            radius += 1

        if not candidates:
            continue

        candidate_points = target_points[candidates]
        deltas = candidate_points - query_points[qi]
        dists = np.sqrt(np.sum(deltas * deltas, axis=1))
        best_i = int(np.argmin(dists))
        matched_index[qi] = candidates[best_i]
        distance[qi] = dists[best_i]

    return matched_index, distance


def _encode_cells(cell_coords):
    """Pack integer (cx, cy, cz) cell coordinates into one int64 id each.

    A coordinate that doesn't fit the safe offset window comes back as -1 --
    a query point wildly far from the target cloud (a gross outlier with no
    real correspondence at all, or simply well outside whatever tolerance
    this call's cell_size represents). -1 can never collide with a real id
    (always >= 0 by construction), so those points naturally resolve to "no
    candidates found" through the normal search path in _query_grid, with no
    special-casing needed there.
    """
    shifted = cell_coords + _CELL_OFFSET
    in_range = np.all((shifted >= 0) & (shifted < _CELL_BASE), axis=1)
    safe_shifted = np.where(in_range[:, None], shifted, 0)
    ids = (safe_shifted[:, 0] * _CELL_BASE + safe_shifted[:, 1]) * _CELL_BASE + safe_shifted[:, 2]
    return np.where(in_range, ids, -1)


def _build_target_grid(target_points, cell_size):
    """Bucket target_points into a uniform grid, sorted by cell id so
    _query_grid can look up a query point's neighbor cells with
    np.searchsorted instead of a per-point Python dict lookup."""
    n_target = len(target_points)
    empty_i64 = np.empty(0, dtype=np.int64)
    if n_target == 0:
        return _TargetGrid(target_points, np.zeros(3), max(cell_size, 1e-9),
                            empty_i64, empty_i64, empty_i64, empty_i64)

    mins = target_points.min(axis=0)
    extent = target_points.max(axis=0) - mins
    cell_size = max(cell_size, float(np.max(extent)) / _MAX_GRID_SPAN, 1e-9)

    cell_coords = np.floor((target_points - mins) / cell_size).astype(np.int64)
    cell_ids = _encode_cells(cell_coords)

    order = np.argsort(cell_ids, kind="stable")
    sorted_ids = cell_ids[order]
    unique_ids, starts, counts = np.unique(sorted_ids, return_index=True, return_counts=True)

    return _TargetGrid(target_points, mins, cell_size, unique_ids, starts, counts, order)


# The 27 (rings=1) neighbor-cell offsets -- cached since the same array is
# reused across every chunk and every call at the only rings value the real
# passes use.
_NEIGHBOR_OFFSETS_CACHE = {}


def _neighbor_offsets(rings):
    offsets = _NEIGHBOR_OFFSETS_CACHE.get(rings)
    if offsets is None:
        offsets = np.array(list(itertools.product(range(-rings, rings + 1), repeat=3)), dtype=np.int64)
        _NEIGHBOR_OFFSETS_CACHE[rings] = offsets
    return offsets


def _query_grid(grid, query_points, rings=1):
    """For each row in `query_points`, find its nearest row in `grid`'s
    target points, searching a fixed (2*rings+1)**3 cell neighborhood --
    vectorized across every query point at once (chunked to bound peak
    memory), not a per-point Python loop.

    Correctness: a target point within Euclidean distance <= rings *
    grid.cell_size of a query point differs from that query's own cell by
    at most `rings` cells along each axis -- a displacement of exactly
    rings*cell_size shifts floor((x-min)/cell_size) by exactly `rings`,
    since floor(a+n) == floor(a)+n for any integer n, and a smaller
    displacement can only shift it less. So the (2*rings+1)**3 neighborhood
    is *guaranteed* to contain every such point; nothing farther away than
    that tolerance can be missed by mistake. `build_vertex_correspondence`
    picks cell_size per pass so that tolerance covers exactly what that
    pass needs (pass 1: a generous multiple of the coarse scale estimate's
    residual; pass 2: match_epsilon itself) -- a point genuinely farther
    than that tolerance correctly comes back unmatched, since nothing
    downstream ever reads a distance beyond the tolerance it was searched
    with anyway.

    Only ever called with rings=1 in this module -- both real passes size
    their cell to the exact tolerance that matters, so a single 3x3x3
    lookup is always sufficient by the argument above; no ring escalation
    needed (or wanted -- see _grid_nearest_neighbor's docstring for why
    _coarse_align's scoring specifically stays off this vectorized path).

    Returns (index, distance) int64/float64 arrays, len(query_points).
    `index` is -1 and `distance` is inf for a query point with no match
    found within the searched neighborhood.
    """
    n_query = len(query_points)
    matched_index = np.full(n_query, -1, dtype=np.int64)
    distance = np.full(n_query, np.inf, dtype=np.float64)
    if n_query == 0 or len(grid.unique_ids) == 0:
        return matched_index, distance

    offsets = _neighbor_offsets(rings)
    n_offsets = len(offsets)
    n_unique = len(grid.unique_ids)

    for chunk_start in range(0, n_query, _QUERY_CHUNK_SIZE):
        chunk_end = min(chunk_start + _QUERY_CHUNK_SIZE, n_query)
        chunk_points = query_points[chunk_start:chunk_end]
        n_chunk = len(chunk_points)

        query_cells = np.floor((chunk_points - grid.mins) / grid.cell_size).astype(np.int64)
        neighbor_cells = (query_cells[:, None, :] + offsets[None, :, :]).reshape(-1, 3)
        flat_ids = _encode_cells(neighbor_cells)

        pos = np.searchsorted(grid.unique_ids, flat_ids)
        pos_clipped = np.clip(pos, 0, n_unique - 1)
        valid = (flat_ids >= 0) & (pos < n_unique) & (grid.unique_ids[pos_clipped] == flat_ids)

        if not np.any(valid):
            continue

        query_idx_per_offset = np.repeat(np.arange(n_chunk), n_offsets)
        valid_query_idx = query_idx_per_offset[valid]
        valid_starts = grid.starts[pos_clipped[valid]]
        valid_counts = grid.counts[pos_clipped[valid]]

        total_candidates = int(valid_counts.sum())
        if total_candidates == 0:
            continue

        # Ragged expansion: turn each (query, neighbor cell) hit -- which
        # covers a *range* of `count` target points in sorted_target_idx --
        # into one row per individual candidate target point.
        cumsum = np.cumsum(valid_counts)
        cell_of_candidate = np.repeat(np.arange(len(valid_counts)), valid_counts)
        offset_within_cell = np.arange(total_candidates) - np.repeat(cumsum - valid_counts, valid_counts)
        flat_sorted_pos = valid_starts[cell_of_candidate] + offset_within_cell

        candidate_target_idx = grid.sorted_target_idx[flat_sorted_pos]
        # valid_query_idx is non-decreasing (built from an arange-repeat,
        # then boolean-masked, which preserves order), so indexing it by the
        # also-non-decreasing cell_of_candidate keeps candidate_query_idx
        # non-decreasing too -- no explicit sort needed to group by query.
        candidate_query_idx = valid_query_idx[cell_of_candidate]

        deltas = grid.target_points[candidate_target_idx] - chunk_points[candidate_query_idx]
        dists = np.sqrt(np.sum(deltas * deltas, axis=1))

        # Stable-sort by (query idx, distance) so each query's nearest
        # candidate lands first within its own group, then take that first
        # row per group with one np.unique call.
        order = np.lexsort((dists, candidate_query_idx))
        sorted_qidx = candidate_query_idx[order]
        unique_qidx, first_pos = np.unique(sorted_qidx, return_index=True)

        matched_index[chunk_start + unique_qidx] = candidate_target_idx[order][first_pos]
        distance[chunk_start + unique_qidx] = dists[order][first_pos]

    return matched_index, distance
