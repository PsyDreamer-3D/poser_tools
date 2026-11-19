# SPDX-License-Identifier: GPL-3.0-or-later
"""
library_index.py
----------------
Phase 7.3a (docs/handoff-morph-injection.md): decides which Poser library files
are morph-injection or removal packages and describes them for the asset index.
No `bpy`: the child Blender process that writes the asset files only reads the
manifest this builds.

Two jobs:

1. Classify every user-facing `.pz2`/`.p2z` under the configured Runtime roots by
   following `readScript` to its leaf files and sniffing the first 16 KB of each.
   16,405 entries take ~8 s on the owner's 20 roots (warm cache).
2. Turn the classified entries that should be shown (injection, removal) into a
   manifest: one group per source folder (one asset `.blend` each), display names,
   catalogs, tags, thumbnails, and a hash per folder so a later build rewrites
   only the folders that changed.

The leaf sniff is a second reader of the CR2 format, like injection_scan.py;
tests/test_library_index.py checks it against the real parser. It only decides
what to list, so a miss hides a package, it never changes what Apply does.
"""

import gzip
import hashlib
import json
import os
import re
import uuid
import zlib
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor

from .cr2_parser import CR2Tokenizer
from .injection_scan import scan_channels
from .poser_io import COMPRESSED_EXTENSIONS, read_poser_file
from .poser_paths import resolve_poser_path
from .runtime_index import walk_roots

# 2: a `targetGeom ... name - hidden 1` visibility file is no longer classified as a removal
# (a removal must declare its empty delta list).
# 3: only files in Poser's own library category folders are entries (POSER_LIBRARY_CATEGORIES).
# 4: a package that moves bones is an "armature" pose and hidden, even if it also injects.
# 5: `thighLength` marks a pose file too (owner's tip; 70 pose files have it but no bone channel).
# 6: MAT poses (`material` blocks and nothing else) are entries too, kind "material" (Phase 8.2).
# Entry answers are cached by file stamps alone, so a rule change must invalidate them here; the
# bump also makes Preferences say the index needs rebuilding.
MANIFEST_VERSION = 6
HEAD_BYTES = 16384
# Orchestrators nest two or three deep in practice (an entry, an "all" file, the leaves).
# The cap only stops a pathological tree; hitting it marks the entry incomplete.
MAX_DEPTH = 8
PACKAGE_EXTENSIONS = {"pz2", "p2z"}
# Morph packages, and MAT poses since Phase 8.2 (all of them, props and clothing included: a
# prop imported through FBX takes a MAT the same way a figure does; owner, 2026-11-18).
SHOWN_KINDS = ("injection", "removal", "material")
# Blender ID names are 63 bytes of UTF-8.
ID_NAME_LIMIT = 63

LEAF_PMD = "pmd"
LEAF_INJECTION = "inj"
LEAF_REMOVAL = "rem"
LEAF_POSE = "morph-values"
LEAF_OTHER = "other"

_INDEXES_RE = re.compile(r"\bindexes\s+[1-9]")
# What a pose file has and a morph file doesn't: a bone channel (`rotateX xrot`, `translateY ytran`
# inside an actor; morph files only carry `targetGeom`/`valueParm` channels), or the `thighLength`
# line Poser writes into pose files (owner's tip: 3,352 files on their library have it, 70 of them
# with no bone channel at all, e.g. the empty second block of a saved pose).
_BONE_CHANNEL_RE = re.compile(r"^[ \t]*(?:(?:rotate|translate)[XYZ][ \t]|thighLength\b)", re.M)
# A MAT pose is `material` blocks and nothing else (owner's rule): no channels, no readScript.
_MATERIAL_BLOCK_RE = re.compile(r"^[ \t]*material[ \t]+\S", re.M)
_CHANNELS_RE = re.compile(r"^[ \t]*channels\b", re.M)
_DELTAS_RE = re.compile(r"\bdeltas\b")


# ---------------------------------------------------------------------------
# Classification
# ---------------------------------------------------------------------------

# The folders under Runtime/Libraries that Poser's Library palette shows. Anything else there
# (`!DAZ`, `!Adz`, `morphs`, ...) is a vendor's store of the files the packages in these folders
# readScript; Poser never lists it, and those files aren't meant to be applied on their own
# (owner, 2026-11-17). On the owner's library all 31 such files that classified as packages were
# read by a package under `pose`, so nothing becomes unreachable.
POSER_LIBRARY_CATEGORIES = frozenset({
    "camera", "character", "face", "hair", "hand", "light", "materials", "pose", "props", "scene",
})


def in_poser_library(category: str) -> bool:
    """True when `category` (a path below Runtime/Libraries) is inside one of Poser's own folders."""
    first = category.replace("\\", "/").split("/", 1)[0].lower()
    return first in POSER_LIBRARY_CATEGORIES


def is_delta_leaf(name: str, category: str) -> bool:
    """True for the per-morph files a package points at, which are never entries themselves."""
    lowered = name.lower()
    return (lowered.startswith(("injdeltas.", "remdeltas."))
            or "/deltas" in "/" + category.lower())


def read_head(path: str):
    """The first 16 KB of a Poser file as text (decompressing `.p2z`), or None if unreadable.

    Line endings are normalized to `\n`, as read_poser_file()'s text mode does: old Mac
    content ends lines with a bare `\r`, and every `^`-anchored pattern here (bone channels,
    material blocks, channel names) missed such files until this (Phase 8.2: 8 MAT poses,
    `DP-Thourobread`, `LongHair_evo_Univ`, read as "other").
    """
    try:
        if os.path.splitext(path)[1].lower() in COMPRESSED_EXTENSIONS:
            try:
                with gzip.open(path, "rb") as fh:
                    return _normalize_newlines(fh.read(HEAD_BYTES).decode("utf-8", "replace"))
            except (OSError, EOFError, zlib.error):
                pass   # a ".p2z" that isn't gzip, which read_poser_file() also tolerates
        with open(path, "rb") as fh:
            return _normalize_newlines(fh.read(HEAD_BYTES).decode("utf-8", "replace"))
    except OSError:
        return None


def _normalize_newlines(text: str) -> str:
    return text.replace("\r\n", "\n").replace("\r", "\n")


def sniff_leaf(head: str) -> str:
    """What a leaf file does, from its first 16 KB.

    Removal is read from a parsed channel, not grepped. `Hide.*` / `Unhide.*` files also
    contain `name -`: on a `valueParm` (a grep put 624 of them on the removal list), and
    on a `targetGeom` beside `hidden 1` (154 more, found only by looking at the library
    in the Asset Browser). What a removal has and a visibility file lacks is a declared,
    empty delta list (`indexes 0`); some vendors' RemDeltas files also carry `hidden 1`,
    so `hidden` can't be the test. Injection stays a pattern match because a channel's
    delta block runs past 16 KB, so the channel never closes inside the head.
    """
    # `removePMDFileMorphs` is the same binary-morph system's removal: La Femme's "REM ..."
    # files are a list of those commands plus `name -`/`hidden` channels, not delta files.
    if ("injectPMDFileMorphs" in head or "removePMDFileMorphs" in head
            or "useBinaryMorph 1" in head):
        return LEAF_PMD
    if "targetGeom" in head and _INDEXES_RE.search(head) and _DELTAS_RE.search(head):
        return LEAF_INJECTION
    if any(c.display_name == "-" and c.delta_count == 0 and c.declares_deltas
           for c in scan_channels(head)):
        return LEAF_REMOVAL
    if "targetGeom" in head:
        return LEAF_POSE
    return LEAF_OTHER


def _stamp(path: str):
    try:
        st = os.stat(path)
    except OSError:
        return None
    return (path, st.st_mtime_ns, st.st_size)


class _Visit:
    def __init__(self, roots, dir_cache, leaf_cache):
        self.roots = roots
        self.dir_cache = dir_cache
        self.leaf_cache = leaf_cache
        self.kinds = Counter()
        self.missing = 0
        self.truncated = False
        self.moves_bones = False
        self.visited = []     # every file read, for the cache stamps


def _visit(path, depth, seen, state):
    if path in seen:
        return
    seen.add(path)
    if depth > MAX_DEPTH:
        state.truncated = True
        return
    state.visited.append(path)

    head = read_head(path)
    if head is None:
        return
    if not state.moves_bones and _BONE_CHANNEL_RE.search(head):
        state.moves_bones = True
    # The cheap substring test first: leaf files are the big ones, and tokenizing
    # them only to learn they hold no directive would dominate the cost.
    tokens = ()
    if "readScript" in head:
        try:
            tokens = CR2Tokenizer.tokenize(read_poser_file(path))
        except OSError:
            return
    if "readScript" not in tokens:
        kind = state.leaf_cache.get(path)
        if kind is None:
            kind = state.leaf_cache[path] = sniff_leaf(head)
        state.kinds[kind] += 1
        return

    for i, token in enumerate(tokens):
        if token != "readScript" or i + 1 >= len(tokens):
            continue
        resolved = resolve_poser_path(
            tokens[i + 1], path, extra_roots=state.roots, dir_cache=state.dir_cache
        )
        if resolved is None:
            state.missing += 1
        else:
            _visit(resolved, depth + 1, seen, state)


def classify_entry(path: str, roots, dir_cache=None, leaf_cache=None) -> dict:
    """Classify one library file by following `readScript` to its leaves.

    `roots` are the configured content roots a target may live in (a package
    routinely names files in another library). `dir_cache` and `leaf_cache` are
    optional caller-owned dicts shared across calls; both make a whole-library
    pass much cheaper because leaf files are shared between entries.

    Returns {"kind", "incomplete", "mixed", "n_files", "n_missing", "stamps"}:
    - kind: "armature" (some file it reads sets bone rotations/translations, or carries
      Poser's pose-file `thighLength` line: a body pose,
      even when it also injects the morphs it dials, like V3 Spellcaster, or a "Fit"
      package that moves the hip as well), "injection" (some leaf has deltas), "pmd"
      (binary morphs, unsupported), "removal" (leaves re-declare channels as `name -`),
      "material" (a MAT pose: one file of `material` blocks and nothing else; Phase 8.2),
      "pose" (dials morph values, no deltas), "missing" (every target unresolved), else
      "other". Injection, removal and material are shown (SHOWN_KINDS).
    - incomplete: some target could not be found, or the tree was too deep.
    - mixed: an injection that also contains removal leaves (it can offer both).
    - stamps: [(path, mtime_ns, size)] of every file read, to tell later whether
      the answer is still valid.
    """
    state = _Visit(list(roots), {} if dir_cache is None else dir_cache,
                   {} if leaf_cache is None else leaf_cache)
    _visit(path, 0, set(), state)

    kinds = state.kinds
    if state.moves_bones:
        # Owner's rule (2026-11-17): the library holds morph packages only. Applying a pose
        # means translating Poser's bone space through the FBX import's bone-roll fixes, which
        # this add-on doesn't do; the morphs it would inject are still reachable through
        # Apply's own dropdown.
        kind = "armature"
    elif kinds[LEAF_INJECTION]:
        kind = "injection"
    elif kinds[LEAF_PMD]:
        kind = "pmd"
    elif kinds[LEAF_REMOVAL]:
        kind = "removal"
    elif kinds[LEAF_POSE]:
        kind = "pose"
    elif not sum(kinds.values()) and state.missing:
        kind = "missing"
    else:
        kind = "other"
    if kind == "other" and state.visited == [path] and _is_mat_pose(path):
        kind = "material"
    return {
        "kind": kind,
        "incomplete": bool(state.missing or state.truncated),
        "mixed": bool(kinds[LEAF_INJECTION] and kinds[LEAF_REMOVAL]),
        "n_files": sum(kinds.values()),
        "n_missing": state.missing,
        "stamps": [s for s in map(_stamp, state.visited) if s is not None],
    }


def _is_mat_pose(path: str) -> bool:
    """`material` blocks and nothing else, judged on the whole file.

    The 16 KB head the rest of classification reads can't prove "nothing else": a shoe MAT runs
    5,000 lines, and a pose-with-materials file could put its channels after them. So a file whose
    head has a material block is read in full; on the owner's library that is ~2,700 small files.
    """
    head = read_head(path)
    if not head or not _MATERIAL_BLOCK_RE.search(head) or _CHANNELS_RE.search(head) or "readScript" in head:
        return False
    try:
        text = read_poser_file(path)
    except OSError:
        return False
    return not _CHANNELS_RE.search(text) and "readScript" not in text


def _stamps_current(stamps) -> bool:
    for path, mtime_ns, size in stamps:
        current = _stamp(path)
        if current is None or current[1] != mtime_ns or current[2] != size:
            return False
    return True


# ---------------------------------------------------------------------------
# Thumbnails
# ---------------------------------------------------------------------------

_IMAGE_EXTENSIONS = (".png", ".jpg", ".jpeg")


def find_thumbnail(path: str, dir_cache=None) -> tuple:
    """(thumbnail path, "image"|"rsr"|"none") for a library file.

    A Poser thumbnail sits next to the file with the same stem. `.rsr` is a classic
    Mac resource fork holding a PICT, which Blender can't read; the asset writer decodes it
    with core/mac_pict.py (Phase 7.3d). An image file wins over an `.rsr`. Case-insensitive,
    one directory listing per folder.
    """
    cache = {} if dir_cache is None else dir_cache
    folder, name = os.path.split(path)
    if folder not in cache:
        try:
            cache[folder] = {n.lower(): n for n in os.listdir(folder)}
        except OSError:
            cache[folder] = {}
    listing = cache[folder]
    stem = os.path.splitext(name)[0].lower()
    for ext in _IMAGE_EXTENSIONS:
        hit = listing.get(stem + ext)
        if hit:
            return os.path.join(folder, hit), "image"
    hit = listing.get(stem + ".rsr")
    if hit:
        return os.path.join(folder, hit), "rsr"
    return "", "none"


# ---------------------------------------------------------------------------
# Names, catalogs, folders
# ---------------------------------------------------------------------------

def _utf8_trim(text: str, limit: int) -> str:
    return text.encode("utf-8")[:limit].decode("utf-8", "ignore")


def compose_name(stem: str, suffix: str = "", limit: int = ID_NAME_LIMIT) -> str:
    """`stem`, or `stem (suffix)`, within `limit` bytes; the suffix gives way first."""
    if not suffix:
        return _utf8_trim(stem, limit)
    full = f"{stem} ({suffix})"
    if len(full.encode("utf-8")) <= limit:
        return full
    room = limit - len(f"{stem} ()…".encode("utf-8"))
    if room >= 4:
        return f"{stem} ({_utf8_trim(suffix, room)}…)"
    return _utf8_trim(full, limit)


def assign_display_names(entries: list) -> None:
    """Set entry["name"] on each shown entry.

    The file stem, plus the parent folder when the same stem occurs more than once
    within the entry's own kind: 60-66% of real entries repeat, so plain names would
    leave most of the grid ambiguous (the two kinds are never listed together, so a
    stem shared with the *other* kind is not a repeat). Names must be unique per asset
    file, one per source folder; trimming can in principle collide two names, so a
    counter settles that. Identity is never the name, it is (root, rel_path).
    """
    repeats = Counter((e["kind"], e["stem"]) for e in entries)
    taken = defaultdict(set)
    for entry in sorted(entries, key=lambda e: e["path"]):
        suffix = os.path.basename(entry["folder"]) if repeats[(entry["kind"], entry["stem"])] > 1 else ""
        name = compose_name(entry["stem"], suffix)
        used = taken[entry["folder"]]
        n = 2
        while name in used:
            tag = f"~{n}"
            name = _utf8_trim(compose_name(entry["stem"], suffix), ID_NAME_LIMIT - len(tag)) + tag
            n += 1
        used.add(name)
        entry["name"] = name


def runtime_names(roots, labels=None) -> dict:
    """{root: the name its catalogs go under}, one per root, all different.

    The user's Label for the root (Preferences) if set, else the folder's own name, which is
    what Poser's Library palette shows. Two roots that would share a name get their parent
    folder appended, then a number, so their catalogs never merge.
    """
    labels = labels or {}
    names, used = {}, set()
    for root in roots:
        folder = os.path.normpath(root)
        base = (labels.get(root) or "").strip() or os.path.basename(folder) or folder
        name = base
        if name.casefold() in used:
            name = f"{base} ({os.path.basename(os.path.dirname(folder))})"
        n = 2
        while name.casefold() in used:
            name, n = f"{base} {n}", n + 1
        used.add(name.casefold())
        names[root] = name
    return names


def catalog_path(category: str, runtime_name: str = "") -> str:
    """The Asset Browser catalog for a library folder: `<runtime>/<path below Libraries>`.

    Grouped by Runtime the way Poser's Library palette is. The category folder right under
    Libraries gets its first letter capitalized (owner's call, 2026-11-17): runtimes spell it
    `pose` or `Pose`, and without this the tree showed both side by side. Deeper folders keep
    their own spelling. The catalog file format uses ':' as a separator, so no name can carry one.
    """
    parts = [p.replace(":", "-").strip() for p in category.replace("\\", "/").split("/")]
    parts = [p for p in parts if p]
    if parts:
        parts[0] = parts[0][:1].upper() + parts[0][1:]
    if runtime_name:
        parts.insert(0, runtime_name.replace(":", "-").replace("/", "-").strip())
    return "/".join(p for p in parts if p)


def blend_name_for(folder: str) -> str:
    """A stable, filesystem-safe asset file name for one source folder."""
    slug = re.sub(r"[^A-Za-z0-9._-]+", "_", os.path.basename(folder)).strip("_")[:40] or "folder"
    digest = hashlib.sha1(os.path.normcase(folder).encode("utf-8")).hexdigest()[:8]
    return f"{slug}-{digest}.blend"


def _folder_hash(entries: list) -> str:
    keys = ("rel_path", "kind", "mixed", "incomplete", "name", "catalog", "description",
            "thumbnail", "thumbnail_mtime_ns")
    payload = json.dumps([[e[k] for k in keys] for e in sorted(entries, key=lambda e: e["path"])],
                         sort_keys=True)
    return hashlib.sha1(payload.encode("utf-8")).hexdigest()


# Namespace for the catalog ids: uuid5 of the catalog path, so an id is stable across rebuilds
# and machines and a rebuild never orphans an asset from its catalog.
_CATALOG_NAMESPACE = uuid.UUID("5f6c1e3a-0b0e-4a52-9d7e-5b7f3c1f2a10")
# Bump when what the asset files contain changes (placeholders, tags, payload keys), so
# existing ones are rebuilt: it goes into the manifest signature.
ASSET_FORMAT_VERSION = 4   # 2: stand-ins are Scenes (the Asset Browser doesn't list Images); 3: kind badge on thumbnails; 4: .rsr thumbnails decoded


def asset_signature(blender_version: str) -> str:
    """The `signature_extra` for build_manifest(): asset format plus the Blender that wrote them."""
    return f"assets-v{ASSET_FORMAT_VERSION}|blender-{blender_version}"


def catalog_uuid(path: str) -> str:
    return str(uuid.uuid5(_CATALOG_NAMESPACE, path))


def catalog_ids(manifest: dict) -> dict:
    """{catalog path: uuid} for every catalog the shown entries use, parents included."""
    paths = set()
    for folder in manifest["folders"]:
        for entry in folder["entries"]:
            parts = entry["catalog"].split("/") if entry["catalog"] else []
            paths.update("/".join(parts[:i]) for i in range(1, len(parts) + 1))
    return {path: catalog_uuid(path) for path in sorted(paths)}


def catalog_file_text(ids: dict) -> str:
    """The body of `blender_assets.cats.txt` for these catalogs."""
    lines = [
        "# This is an Asset Catalog Definition file for Blender.",
        "#",
        "# Empty lines and lines starting with `#` will be ignored.",
        "# The first non-ignored line should be the version indicator.",
        '# Other lines are of the format "UUID:catalog/path/for/assets:simple catalog name"',
        "",
        "VERSION 1",
        "",
    ]
    lines += [f"{uid}:{path}:{path.rsplit('/', 1)[-1]}" for path, uid in sorted(ids.items())]
    return "\n".join(lines) + "\n"


# ---------------------------------------------------------------------------
# Manifest
# ---------------------------------------------------------------------------

PREFETCH_WORKERS = 32
_PREFETCH_CHUNK = 512


def prefetch_heads(paths, cancelled=None, progress=None, workers=PREFETCH_WORKERS) -> None:
    """Read the head of every file concurrently so the classification pass finds them cached.

    A cold build is bound by ~17,000 small reads from a slow drive, not by CPU: on the
    owner's external drive a serial build took 64.5 s with cold file contents (9.3 s
    warm), and 32 reader threads cut it to 38 s. Failures are ignored, the real read
    reports them. Checks `cancelled` between chunks.
    """
    def touch(path):
        try:
            with open(path, "rb") as fh:
                fh.read(HEAD_BYTES)
        except OSError:
            pass

    with ThreadPoolExecutor(workers) as pool:
        for start in range(0, len(paths), _PREFETCH_CHUNK):
            if cancelled is not None and cancelled():
                raise BuildCancelled()
            list(pool.map(touch, paths[start:start + _PREFETCH_CHUNK]))
            if progress is not None:
                progress("reading", min(start + _PREFETCH_CHUNK, len(paths)), len(paths))


class BuildCancelled(Exception):
    """Raised by build_manifest() when its `cancelled()` callback says stop."""


def make_signature(roots, extra=None) -> dict:
    """What a manifest's answers depend on besides the files: the ordered roots and the format."""
    return {"version": MANIFEST_VERSION, "roots": list(roots), "extra": extra}


def _tags(kind: str, mixed: bool, incomplete: bool) -> list:
    tags = [kind]
    if mixed and "removal" not in tags:
        tags.append("removal")
    if incomplete:
        tags.append("incomplete")
    tags.append("poser")
    return tags


def _describe(rel_path: str, rec: dict) -> str:
    if rec["incomplete"]:
        total = rec["n_files"] + rec["n_missing"]
        return f"[INCOMPLETE: {rec['n_missing']} of {total} files missing] {rel_path}"
    return rel_path


def build_manifest(roots, previous=None, signature_extra=None, progress=None, cancelled=None,
                   prefetch=True, runtime_labels=None) -> dict:
    """Classify the library under `roots` and describe what should be indexed.

    `previous` is an earlier manifest: an entry whose file and every leaf it read are
    unchanged (mtime and size) is reused instead of re-read, which is what makes a
    rebuild cheap on a slow drive. A different signature (roots, format) discards it.
    `progress(phase, done, total)` ("reading" then "classifying") and `cancelled()` are
    optional callbacks; cancelling raises BuildCancelled. With no usable `previous`, the
    package files' heads are read in parallel first (`prefetch=False` to skip).
    `runtime_labels` ({root: label}) name the top-level catalogs (runtime_names()); they are
    layout only, recorded as "layout" and kept out of the signature, so renaming one reuses
    every classification and rewrites only the folders whose catalog changed.

    Returns {"version", "signature", "classified": {path: record}, "folders": [...],
    "stats": {...}}. Only injection and removal entries appear in "folders"; "classified"
    holds every entry so hidden ones are not re-read next time either.
    """
    roots = [r for r in dict.fromkeys(roots) if r and os.path.isdir(r)]
    signature = make_signature(roots, signature_extra)
    names = runtime_names(roots, runtime_labels)
    prior = {}
    if previous and previous.get("signature") == signature:
        prior = previous.get("classified", {})

    candidates = [
        (root, item) for root, item in walk_roots(roots)
        if item.ext in PACKAGE_EXTENSIONS and in_poser_library(item.category)
        and not is_delta_leaf(item.name, item.category)
    ]

    if prefetch and not prior:
        everything = [item.filepath for _root, item in walk_roots(roots) if item.ext in PACKAGE_EXTENSIONS]
        prefetch_heads(everything, cancelled=cancelled, progress=progress)

    dir_cache, leaf_cache, thumb_cache = {}, {}, {}
    classified, shown = {}, []
    reused = 0
    for n, (root, item) in enumerate(candidates):
        if cancelled is not None and cancelled():
            raise BuildCancelled()
        if progress is not None and n % 200 == 0:
            progress("classifying", n, len(candidates))

        rec = prior.get(item.filepath)
        if rec is not None and _stamps_current(rec["stamps"]):
            reused += 1
        else:
            rec = classify_entry(item.filepath, roots, dir_cache, leaf_cache)
        classified[item.filepath] = rec

        if rec["kind"] in SHOWN_KINDS:
            thumb, thumb_kind = find_thumbnail(item.filepath, thumb_cache)
            rel_path = os.path.relpath(item.filepath, root).replace("\\", "/")
            thumb_stamp = _stamp(thumb) if thumb else None
            shown.append({
                "path": item.filepath,
                "root": root,
                "rel_path": rel_path,
                "folder": os.path.dirname(item.filepath),
                "catalog": catalog_path(item.category, names[root]),
                "stem": item.name,
                "ext": item.ext,
                "kind": rec["kind"],
                "mixed": rec["mixed"],
                "incomplete": rec["incomplete"],
                "n_files": rec["n_files"],
                "n_missing": rec["n_missing"],
                "tags": _tags(rec["kind"], rec["mixed"], rec["incomplete"]),
                "description": _describe(rel_path, rec),
                "thumbnail": thumb,
                "thumbnail_kind": thumb_kind,
                "thumbnail_mtime_ns": thumb_stamp[1] if thumb_stamp else 0,
            })
    if progress is not None:
        progress("classifying", len(candidates), len(candidates))

    assign_display_names(shown)
    by_folder = defaultdict(list)
    for entry in shown:
        by_folder[entry["folder"]].append(entry)
    folders = [
        {"dir": folder, "blend": blend_name_for(folder), "hash": _folder_hash(entries),
         "entries": sorted(entries, key=lambda e: e["name"])}
        for folder, entries in sorted(by_folder.items())
    ]

    return {
        "version": MANIFEST_VERSION,
        "signature": signature,
        "layout": names,
        "classified": classified,
        "folders": folders,
        "stats": {
            "entries": len(candidates),
            "reused": reused,
            "classified": len(candidates) - reused,
            "kinds": dict(Counter(r["kind"] for r in classified.values())),
            "shown": len(shown),
            "folders": len(folders),
            "thumbnails": dict(Counter(e["thumbnail_kind"] for e in shown)),
        },
    }


def changed_folders(previous, manifest) -> dict:
    """What to do to the asset files going from `previous` to `manifest`.

    {"write": [folder dicts whose file is new or whose hash changed],
     "remove": [blend file names that no longer have a folder]}.
    With no previous manifest, or a different signature, everything is written.
    """
    old = {}
    if previous and previous.get("signature") == manifest["signature"]:
        old = {f["blend"]: f["hash"] for f in previous.get("folders", [])}
    write = [f for f in manifest["folders"] if old.get(f["blend"]) != f["hash"]]
    keep = {f["blend"] for f in manifest["folders"]}
    stale = [f["blend"] for f in (previous or {}).get("folders", []) if f["blend"] not in keep]
    return {"write": write, "remove": stale}


# ---------------------------------------------------------------------------
# Persistence. Stamps repeat the same leaf paths across entries (an "all morphs"
# file and each product's own file both read the same leaves), so on disk every
# path is written once and entries refer to it by index.
# ---------------------------------------------------------------------------

def save_manifest(path: str, manifest: dict) -> None:
    table, index = [], {}

    def ref(p):
        if p not in index:
            index[p] = len(table)
            table.append(p)
        return index[p]

    classified = {
        entry: {**rec, "stamps": [[ref(p), m, s] for p, m, s in rec["stamps"]]}
        for entry, rec in manifest["classified"].items()
    }
    payload = {**manifest, "classified": classified, "paths": table}
    temp = path + ".tmp"
    # Gzipped: the real library's manifest is 12.6 MB as JSON and 0.6 MB compressed.
    with gzip.open(temp, "wt", encoding="utf-8", compresslevel=6) as fh:
        json.dump(payload, fh, ensure_ascii=False, separators=(",", ":"))
    os.replace(temp, path)


def load_manifest(path: str):
    """The manifest saved at `path`, or None if missing, unreadable, corrupt or from another format."""
    try:
        with gzip.open(path, "rt", encoding="utf-8") as fh:
            data = json.load(fh)
        if data.get("version") != MANIFEST_VERSION:
            return None
        table = data.pop("paths")
        for rec in data["classified"].values():
            rec["stamps"] = [(table[i], m, s) for i, m, s in rec["stamps"]]
        return data
    except (OSError, EOFError, zlib.error, ValueError, KeyError, IndexError, TypeError):
        return None
