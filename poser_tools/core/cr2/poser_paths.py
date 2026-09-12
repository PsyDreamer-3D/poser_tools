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


def resolve_poser_path(colon_path: str, reference_file: str, extra_roots=None) -> Optional[str]:
    """Resolve a Poser colon-path to an absolute filesystem path.

    Args:
        colon_path: e.g. ":Runtime:libraries:!DAZ:...:InjDeltas.X.pz2"
        reference_file: the file that referenced this path (a readScript
            directive's own file, or a CR2's own path) -- used to find the
            Runtime root via find_runtime_root().
        extra_roots: additional Poser content roots to also try (each one
            the directory that contains its own Runtime folder). Not used by
            any caller yet -- kept for a future addon-preferences setting.

    Returns the first candidate that exists on disk, or None.
    """
    parts = _colon_path_to_parts(colon_path)
    if not parts:
        return None
    relative = os.path.join(*parts)

    candidates = []

    root = find_runtime_root(reference_file)
    if root:
        candidates.append(os.path.join(root, relative))

    if extra_roots:
        for extra_root in extra_roots:
            if extra_root:
                candidates.append(os.path.join(extra_root, relative))

    for candidate in candidates:
        if os.path.isfile(candidate):
            return os.path.abspath(candidate)

    return None
