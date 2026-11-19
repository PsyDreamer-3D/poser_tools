# SPDX-License-Identifier: GPL-3.0-or-later
"""
texture_paths.py
----------------
Finding the texture a Poser material names, the way Poser does before it asks the user
(Phase 8.3, docs/handoff-morph-injection.md). No bpy.

A MAT pose's texture reference is meant to be a colon path from the content root
(`:Runtime:Textures:DAZ:...`), but on the owner's library 45% of them don't resolve that way
(10,469 of 23,294, in 863 MAT poses):
- written relative to `Runtime:Textures` (`:DAZ:Characters:...`, `:Stonemason:...`), or to
  `Runtime` (`:textures:DAZ:...`): Poser resolves both (owner);
- absolute Windows paths from the author's machine (`D:\\Program Files\\...\\Runtime\\Textures\\p3d\\x.jpg`);
- bare file names (`catsuit-lace-trans-01.jpg`);
- `NO_MAP`, Poser's "no texture", sometimes written as a path (`:NO_MAP:`);
- `.bum` files, Poser's own bump format: none of the 685 referenced exists, but each has its
  source image beside the name. A .bum is an uncompressed Windows BMP that Poser computes from that
  image (all 18 on the owner's drive): 24-bit ones are a tangent-space normal map with Z dropped
  (R and G are the normal's X and Y, blue 0; X²+Y² never exceeds 1, owner spotted the normal-map
  look), 8-bit ones are the source's greyscale as is. So the source image is the bump map to use;
  a .bum read as a height map would be wrong.
resolve_texture() tries, in order: the path as written; relative to Runtime, then to
Runtime:Textures; a Windows path's tail after its `Runtime\\`; and last, the file name anywhere
under the content roots, preferring the match that shares the most trailing folders with the
reference, then the one in the MAT's own content root. Every step past the first is returned
as a note, so the report says what was substituted instead of doing it silently.
"""

import os
import re

from .poser_paths import find_runtime_root, resolve_poser_path

_WINDOWS_ABSOLUTE_RE = re.compile(r"^[A-Za-z]:[\\/]")
_SEPARATORS_RE = re.compile(r"[:\\/]+")
IMAGE_EXTENSIONS = (".jpg", ".jpeg", ".png", ".tif", ".tiff", ".bmp", ".tga", ".gif")


def is_no_map(reference: str) -> bool:
    """Poser's "no texture", however it was written (`NO_MAP`, `":NO_MAP:"`)."""
    return reference.strip().strip('"').strip(":").upper() == "NO_MAP"


def _parts(reference: str) -> list:
    return [p for p in _SEPARATORS_RE.split(reference) if p]


class TextureIndex:
    """Every image under the content roots by lower-case file name, built on first use.

    One walk of the roots (the owner's 13 hold ~23,000 images; under a second warm). Built
    lazily because most textures resolve by path and never need it.
    """

    def __init__(self, roots):
        self.roots = [r for r in dict.fromkeys(roots or []) if r and os.path.isdir(r)]
        self._by_name = None

    def lookup(self, file_name: str) -> list:
        if self._by_name is None:
            self._by_name = {}
            for root in self.roots:
                for folder, _dirs, files in os.walk(root):
                    for name in files:
                        if name.lower().endswith(IMAGE_EXTENSIONS):
                            self._by_name.setdefault(name.lower(), []).append(os.path.join(folder, name))
        return list(self._by_name.get(file_name.lower(), ()))


def _shared_tail(reference_dirs, candidate: str) -> int:
    """How many of the reference's last folders the candidate's folders end with (case ignored)."""
    candidate_dirs = [p.lower() for p in os.path.normpath(os.path.dirname(candidate)).split(os.sep) if p]
    count = 0
    for ref, cand in zip(reversed([d.lower() for d in reference_dirs]), reversed(candidate_dirs)):
        if ref != cand:
            break
        count += 1
    return count


def _pick_by_name(reference: str, source_file: str, index: TextureIndex) -> tuple:
    parts = _parts(reference)
    if not parts:
        return None, ""
    candidates = sorted(index.lookup(parts[-1]))
    if not candidates:
        return None, ""
    own_root = os.path.normcase(find_runtime_root(source_file) or "")
    def rank(path):
        in_own = bool(own_root) and os.path.normcase(path).startswith(own_root + os.sep)
        return (-_shared_tail(parts[:-1], path), not in_own)
    best = min(candidates, key=rank)
    note = "found by file name"
    if len(candidates) > 1:
        note += f", chosen from {len(candidates)} files of that name"
    return best, note


def resolve_texture(reference: str, source_file: str, roots=None, index: TextureIndex = None,
                    dir_cache=None) -> tuple:
    """(path or None, note) for a texture reference in `source_file`.

    note is "" when the reference resolved as written, "no map" for Poser's NO_MAP, a short
    description of the fallback that found it, or "not found".
    """
    ref = (reference or "").strip().strip('"')
    if not ref or is_no_map(ref):
        return None, "no map"
    stem, ext = os.path.splitext(ref)
    if ext.lower() == ".bum":
        # Blender can't read a .bum; the image Poser built it from is the bump map to use.
        for image_ext in IMAGE_EXTENSIONS:
            found, note = resolve_texture(stem + image_ext, source_file, roots, index, dir_cache)
            if found:
                return found, "Poser .bum bump map, used the image it was made from" + (f" ({note})" if note else "")
        if resolve_poser_path(ref if ref.startswith(":") else ":" + ref, source_file, extra_roots=roots,
                              dir_cache=dir_cache) and not _WINDOWS_ABSOLUTE_RE.match(ref):
            # The .bum is there but its source image isn't. It is a normal map missing its out-of-surface
            # channel (Poser's Y, Poser being Y-up; Blender's Z), with X inverted against Blender's
            # tangent convention by correlation; not a height map.
            # converting it isn't done, since no .bum on the owner's drive lacks its image.
            return None, "Poser .bum without the image it was made from (a normal map with no Z channel, not converted)"
        return None, "not found"

    def colon(path):
        return resolve_poser_path(path, source_file, extra_roots=roots, dir_cache=dir_cache)

    if _WINDOWS_ABSOLUTE_RE.match(ref):
        parts = _parts(ref)
        lowered = [p.lower() for p in parts]
        if "runtime" in lowered:
            cut = len(lowered) - 1 - lowered[::-1].index("runtime")
            tail = parts[cut:]
            if len(tail) > 1:
                found = colon(":" + ":".join(tail))
                if found:
                    return found, "absolute Windows path, found under its Runtime folder"
    elif ":" in ref:
        found = colon(ref if ref.startswith(":") else ":" + ref)
        if found:
            return found, "" if ref.startswith(":") else "colon path without its leading ':'"
        rest = ref.lstrip(":")
        if not rest.lower().startswith("runtime:"):
            found = colon(":Runtime:" + rest)
            if found:
                return found, "relative to Runtime"
            found = colon(":Runtime:Textures:" + rest)
            if found:
                return found, "relative to Runtime:Textures"

    if index is not None:
        found, note = _pick_by_name(ref, source_file, index)
        if found:
            return found, note
    return None, "not found"
