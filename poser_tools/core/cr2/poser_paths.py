# SPDX-License-Identifier: MIT
"""
poser_paths.py
--------------
Resolves Poser's colon-separated runtime paths (e.g.
``:Runtime:libraries:!DAZ:A3-H3MorExp1:Deltas:InjDeltas.DC_39_BrowHeavy.pz2``)
to real filesystem paths.

Adapted from cr2_importer's PoserPathResolver (core/obj_loader.py), trimmed to
what poser_tools actually needs right now -- no addon-preferences "extra
roots" support yet (poser_tools has no such preferences UI), but the
`extra_roots` parameter is kept so adding that support later doesn't need an
API change.

Confirmed grammar (docs/handoff-morph-injection.md Phase 3, read directly off
real injection packages): a colon-path is always relative to the "Poser
content root" -- the directory that *contains* a folder literally named
"Runtime" -- and the file referencing it (an orchestrator .pz2's readScript,
or a CR2's figureResFile) always lives somewhere under that same Runtime
folder, so walking up from the referencing file to the nearest ancestor
named "Runtime" and taking its parent finds the root without guessing.
"""

import os
from typing import Optional


def find_runtime_root(reference_file: str) -> Optional[str]:
    """Walk up from `reference_file` to the nearest ancestor directory
    literally named "Runtime", and return its parent (the Poser content
    root -- what a colon-path like ":Runtime:libraries:..." is relative to).

    Returns None if no such ancestor exists.
    """
    current = os.path.dirname(os.path.abspath(reference_file))
    while True:
        if os.path.basename(current).lower() == "runtime":
            return os.path.dirname(current)
        parent = os.path.dirname(current)
        if parent == current:
            return None
        current = parent


def _colon_path_to_parts(colon_path: str) -> list:
    parts = colon_path.replace("\\", ":").replace("/", ":").split(":")
    return [p for p in parts if p]


def _listing(directory: str, dir_cache: Optional[dict]) -> dict:
    """{lowercased name: [real names, sorted]} for `directory`; {} if unreadable."""
    if dir_cache is not None and directory in dir_cache:
        return dir_cache[directory]
    try:
        names = sorted(os.listdir(directory))
    except OSError:
        names = []
    folded: dict = {}
    for name in names:
        folded.setdefault(name.lower(), []).append(name)
    if dir_cache is not None:
        dir_cache[directory] = folded
    return folded


def _resolve_ignoring_case(root: str, parts: list, dir_cache: Optional[dict]) -> Optional[str]:
    """Walk `parts` down from `root`, matching each component ignoring case.

    An exact-case entry wins over a case-variant sibling. The walk does not
    backtrack: if "Pose" and "pose" both exist and the exact one lacks the
    file, a hit under the other is not found -- content that duplicates a
    folder differing only by case can't exist on the case-insensitive
    filesystems Poser content is authored on.
    """
    current = root
    for part in parts:
        matches = _listing(current, dir_cache).get(part.lower())
        if not matches:
            return None
        current = os.path.join(current, part if part in matches else matches[0])
    return current if os.path.isfile(current) else None


def resolve_poser_path(colon_path: str, reference_file: str, extra_roots=None,
                       dir_cache: Optional[dict] = None) -> Optional[str]:
    """Resolve a Poser colon-path to an absolute filesystem path.

    Poser content is authored on case-insensitive filesystems, so a colon-path's
    casing routinely differs from what's on disk (":Runtime:Libraries:Pose:" vs.
    a real "libraries/pose"). On a case-sensitive filesystem a literal lookup
    misses those, so an exact match is tried first and, failing that, each
    component is matched ignoring case. The returned path uses the on-disk
    casing.

    Args:
        colon_path: e.g. ":Runtime:libraries:!DAZ:...:InjDeltas.X.pz2"
        reference_file: the file that referenced this path (a readScript
            directive's own file, or a CR2's own path) -- used to find the
            Runtime root via find_runtime_root().
        extra_roots: additional Poser content roots to also try (each one
            the directory that contains its own Runtime folder). Not used by
            any caller yet -- kept for a future addon-preferences setting.
        dir_cache: optional dict the caller owns, reused across calls to avoid
            re-listing the same directories (an orchestrator can reference
            hundreds of files in a handful of folders). Entries are never
            invalidated, so scope it to one load, not the whole session.

    Returns the first match that exists on disk, or None.
    """
    parts = _colon_path_to_parts(colon_path)
    if not parts:
        return None
    relative = os.path.join(*parts)

    roots = []

    root = find_runtime_root(reference_file)
    if root:
        roots.append(root)

    if extra_roots:
        roots.extend(extra_root for extra_root in extra_roots if extra_root)

    for root in roots:
        candidate = os.path.join(root, relative)
        if os.path.isfile(candidate):
            return os.path.abspath(candidate)

    for root in roots:
        found = _resolve_ignoring_case(root, parts, dir_cache)
        if found:
            return os.path.abspath(found)

    return None
