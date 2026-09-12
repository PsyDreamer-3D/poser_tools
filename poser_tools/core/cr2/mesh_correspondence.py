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
"""

import itertools
from collections import defaultdict

import numpy as np

# Aim for roughly this many target points per grid cell -- few enough that a
# cell's occupant list stays cheap to scan, many enough that we don't spend
# most of the search expanding empty rings.
_TARGET_POINTS_PER_CELL = 4.0


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
    transform, source_centroid, target_centroid = _coarse_align(source_points, target_points)
    perm = transform["permutation"]
    signs = np.array(transform["signs"])
    scale = transform["scale"]

    source_centered = source_points - source_centroid
    target_centered = target_points - target_centroid
    permuted_signed_source = source_centered[:, perm] * signs

    aligned_source = permuted_signed_source * scale
    matched_index, distance = _grid_nearest_neighbor(aligned_source, target_centered)

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
            matched_index, distance = _grid_nearest_neighbor(aligned_source, target_centered)
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
    return transform, source_centroid, target_centroid


def _grid_nearest_neighbor(query_points, target_points, max_radius=None):
    """For each row in `query_points`, find its nearest row in `target_points`
    by bucketing `target_points` into a uniform grid and expanding the
    searched cell radius outward until a match is found (or the search
    exhausts the grid, meaning the query point has no plausible match).

    `max_radius` caps how many rings the search is allowed to expand through
    before giving up on a query point; left as None it defaults to the
    grid's own footprint (i.e. "search everywhere before giving up" -- what
    the final, real match needs). `_coarse_align` passes a small explicit
    cap instead: under a *wrong* permutation/sign candidate, points don't
    correspond at all, so every one of its query points would otherwise
    expand all the way out to that full-footprint radius before giving up --
    paying for an exhaustive search 47 times over just to prove 47 of the 48
    candidates are wrong. A small cap makes a wrong candidate fail fast
    (most of its points find nothing within a few rings) while a correct
    candidate -- whose points sit right on top of their match -- is
    unaffected, since it never needs more than a ring or two anyway.

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
