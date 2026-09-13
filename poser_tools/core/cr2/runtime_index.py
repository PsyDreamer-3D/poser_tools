# SPDX-License-Identifier: MIT
"""
runtime_index.py
-----------------
Poser Runtime library scanner and JSON cache -- finds figure CR2s and
injection-candidate PZ2s under one or more configured Runtime roots, so
operators/applyMorphInjection.py can offer a searchable picker instead of a
raw file dialog.

Adapted from cr2_importer's core/runtime_index.py (same JSON-cache,
incremental-mtime-diffing design), with two deliberate differences:

- No top-level library-folder allowlist. cr2_importer only walks 10 canonical
  category folders (character, props, pose, ...) and explicitly skips
  vendor-prefixed folders like "!DAZ" -- but that's exactly where real
  injection content lives (Runtime/libraries/!DAZ/A3-H3MorExp1/Deltas/
  InjDeltas.*.pz2, confirmed against real content and already encoded in
  tests/test_poser_paths.py). This walks the entire Runtime/Libraries/ tree.
- No thumbnail scanning. The picker this feeds is a text search dropdown
  (prop_search()), not a thumbnail grid, so there's no consumer for one.

Pure Python module (no bpy at module level) so it stays unit-testable
outside Blender. bpy is imported lazily only inside get_cache_dir().
"""

import hashlib
import json
import os
import time
from dataclasses import asdict, dataclass
from typing import Dict, List, Optional

CACHE_VERSION = 1

# Figures (.cr2/.crz) and injection-candidate packages (.pz2/.p2z) only --
# poser_tools doesn't browse props/hair/lights/poses.
SUPPORTED_EXTENSIONS = {".cr2", ".crz", ".pz2", ".p2z"}


@dataclass
class LibraryItem:
    name: str        # Human-readable display name (filename stem)
    filepath: str    # Absolute OS path to the library file
    category: str    # Relative sub-path within Libraries/, forward slashes
    ext: str         # Lowercase, no dot: "cr2", "crz", "pz2", "p2z"
    mtime: float     # os.path.getmtime() value at scan time


def get_cache_dir() -> str:
    """Return (and create) poser_tools' own per-addon config directory for
    cache files. Calls bpy.utils.user_resource at invocation time so this
    module stays importable in non-Blender environments."""
    import bpy  # noqa: PLC0415
    path = bpy.utils.user_resource("CONFIG", path="poser_tools")
    os.makedirs(path, exist_ok=True)
    return path


def _cache_path_for_root(root: str) -> str:
    slug = hashlib.md5(os.path.normcase(root).encode("utf-8")).hexdigest()[:12]
    return os.path.join(get_cache_dir(), f"runtime_index_{slug}.json")


def _item_name(filepath: str) -> str:
    return os.path.splitext(os.path.basename(filepath))[0]


def load_cache(root: str) -> Optional[dict]:
    """Load and return the raw cache dict for root, or None if missing,
    unreadable, invalid JSON, or from an older CACHE_VERSION."""
    cache_file = _cache_path_for_root(root)
    if not os.path.isfile(cache_file):
        return None
    try:
        with open(cache_file, "r", encoding="utf-8") as fh:
            data = json.load(fh)
        if data.get("version") != CACHE_VERSION:
            return None
        return data
    except (OSError, json.JSONDecodeError, KeyError):
        return None


def save_cache(root: str, items: List[LibraryItem]) -> None:
    """Persist items to the JSON cache for root. Failure is non-fatal."""
    cache_file = _cache_path_for_root(root)
    payload = {
        "version": CACHE_VERSION,
        "root": root,
        "scanned_at": time.time(),
        "items": [asdict(i) for i in items],
    }
    try:
        with open(cache_file, "w", encoding="utf-8") as fh:
            json.dump(payload, fh, ensure_ascii=False, indent=2)
    except OSError:
        pass


def _find_subdir_icase(parent: str, name: str) -> Optional[str]:
    try:
        entries = os.listdir(parent)
    except OSError:
        return None
    name_lower = name.lower()
    for entry in entries:
        if entry.lower() == name_lower and os.path.isdir(os.path.join(parent, entry)):
            return os.path.join(parent, entry)
    return None


def _walk_libraries(libraries_dir: str) -> List[LibraryItem]:
    """Scan every file under libraries_dir (no folder allowlist -- see module
    docstring) and return one LibraryItem per supported extension."""
    items: List[LibraryItem] = []
    for dirpath, _dirs, filenames in os.walk(libraries_dir):
        rel = os.path.relpath(dirpath, libraries_dir)
        category = "" if rel == "." else rel.replace("\\", "/")
        for fname in filenames:
            ext_lower = os.path.splitext(fname)[1].lower()
            if ext_lower not in SUPPORTED_EXTENSIONS:
                continue
            full = os.path.join(dirpath, fname)
            try:
                mtime = os.path.getmtime(full)
            except OSError:
                continue
            items.append(LibraryItem(
                name=_item_name(full),
                filepath=full,
                category=category,
                ext=ext_lower.lstrip("."),
                mtime=mtime,
            ))
    return items


def index_runtime(root: str, force: bool = False) -> List[LibraryItem]:
    """Return the item list for a runtime root, using an incremental cache.

    1. If Runtime/Libraries/ doesn't exist -> return [].
    2. If force=True or cache missing/invalid -> full scan + save.
    3. Otherwise: os.walk pass comparing mtime per file vs. the cache --
       unchanged files reuse the cached entry, new/changed files rebuild it,
       deleted files are dropped.
    4. Save the updated cache only if anything actually changed.
    """
    runtime_dir = _find_subdir_icase(root, "Runtime")
    if runtime_dir is None:
        return []
    libraries_dir = _find_subdir_icase(runtime_dir, "Libraries")
    if libraries_dir is None:
        return []

    if force:
        items = _walk_libraries(libraries_dir)
        save_cache(root, items)
        return items

    cached = load_cache(root)
    if cached is None:
        items = _walk_libraries(libraries_dir)
        save_cache(root, items)
        return items

    cached_map: Dict[str, dict] = {
        entry["filepath"]: entry for entry in cached.get("items", [])
    }

    fresh: List[LibraryItem] = []
    changed = False

    for dirpath, _dirs, filenames in os.walk(libraries_dir):
        rel = os.path.relpath(dirpath, libraries_dir)
        category = "" if rel == "." else rel.replace("\\", "/")
        for fname in filenames:
            ext_lower = os.path.splitext(fname)[1].lower()
            if ext_lower not in SUPPORTED_EXTENSIONS:
                continue
            full = os.path.join(dirpath, fname)
            try:
                mtime = os.path.getmtime(full)
            except OSError:
                continue
            cached_entry = cached_map.get(full)
            if cached_entry and cached_entry.get("mtime") == mtime:
                fresh.append(LibraryItem(**cached_entry))
            else:
                fresh.append(LibraryItem(
                    name=_item_name(full),
                    filepath=full,
                    category=category,
                    ext=ext_lower.lstrip("."),
                    mtime=mtime,
                ))
                changed = True

    fresh_paths = {i.filepath for i in fresh}
    if len(fresh_paths) != len(cached_map):
        changed = True

    if changed:
        save_cache(root, fresh)

    return fresh


def get_all_items(roots: List[str]) -> List[LibraryItem]:
    """Aggregate items across all runtime roots (incremental per root).
    De-duplicates by filepath: first root wins if the same file appears in
    more than one configured root."""
    seen: set = set()
    result: List[LibraryItem] = []
    for root in roots:
        if not root:
            continue
        for item in index_runtime(root):
            if item.filepath not in seen:
                seen.add(item.filepath)
                result.append(item)
    return result
