# SPDX-License-Identifier: GPL-3.0-or-later
"""
library_build.py
----------------
Phase 7.3b: turns a library manifest (core/cr2/library_index.py) into asset `.blend` files
and registers the folder as a Blender asset library. Nothing here imports `bpy` at module
level: the Blender binary and the library collection are passed in, so the planning, process
control and registration logic runs under plain pytest. The files themselves are written by
a child Blender process running core/library_assets_child.py.

Everything is written under one directory the add-on owns (see library_dir()), never into the
user's Poser content: the library is theirs, the index is derived data.
"""

import json
import os
import queue
import re
import subprocess
import threading
import time

from .cr2 import library_index

# The name the asset library gets unless the user sets another in Preferences (owner, 2026-11-18;
# was "Poser Morph Packages" until MAT poses joined the library). The add-on finds its own entry by
# folder, not by name (find_asset_library), so the name is free to change.
DEFAULT_LIBRARY_NAME = "Poser"
MANIFEST_FILE = "poser_index.json.gz"
STATUS_FILE = "status.json"
CATALOG_FILE = "blender_assets.cats.txt"
_JOB_FILE = ".job.json"
_CHILD_SCRIPT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "library_assets_child.py")


class AssetBuildError(Exception):
    """The child Blender process failed; the previous index, if any, is left as it was."""


def library_dir(create: bool = True) -> str:
    """The directory holding the asset files, catalog and manifest (created if `create`).

    The same per-add-on config folder runtime_index already keeps its cache in, which is
    per Blender version. bpy is imported here, not at module level, so the rest of this
    module stays importable without Blender.
    """
    import bpy  # noqa: PLC0415
    path = bpy.utils.user_resource("CONFIG", path=os.path.join("poser_tools", "library"))
    if create:
        os.makedirs(path, exist_ok=True)
    return path


def manifest_path(out_dir: str) -> str:
    return os.path.join(out_dir, MANIFEST_FILE)


# ---------------------------------------------------------------------------
# Status: what the Preferences panel shows, kept small so drawing never loads the manifest
# ---------------------------------------------------------------------------

_status_cache = {}


def write_status(out_dir: str, manifest: dict, seconds: float, now=None) -> None:
    """Record the headline numbers of a finished build next to the manifest."""
    status = {"built_at": time.time() if now is None else now, "seconds": round(seconds, 1),
              "signature": manifest["signature"], "layout": manifest.get("layout"),
              "stats": manifest["stats"]}
    target = os.path.join(out_dir, STATUS_FILE)
    temp = target + ".tmp"
    with open(temp, "w", encoding="utf-8") as fh:
        json.dump(status, fh)
    os.replace(temp, target)


def read_status(out_dir: str):
    """The last build's status, or None. Cached on the file's mtime: Preferences redraws often."""
    target = os.path.join(out_dir, STATUS_FILE)
    try:
        stamp = os.stat(target).st_mtime_ns
    except OSError:
        return None
    cached = _status_cache.get(target)
    if cached and cached[0] == stamp:
        return cached[1]
    try:
        with open(target, "r", encoding="utf-8") as fh:
            status = json.load(fh)
        status["signature"], status["stats"], status["built_at"]
    except (OSError, ValueError, KeyError, TypeError):
        return None
    _status_cache[target] = (stamp, status)
    return status


def status_is_stale(status: dict, roots, blender_version: str, labels=None) -> bool:
    """True when the roots, their names, the asset format or the Blender version differ from
    the last build's.

    A build against a different set answers different questions (a target may now resolve), so
    the index must be rebuilt. A renamed Runtime only moves catalogs; an update rewrites just
    those folders. New *content* inside the same roots cannot be seen cheaply; that is what the
    always-available, incremental update button is for. A status from before Runtime-level
    catalogs has no layout and reads as stale.
    """
    existing = [r for r in dict.fromkeys(roots) if r and os.path.isdir(r)]
    current = library_index.make_signature(existing, library_index.asset_signature(blender_version))
    return (status["signature"] != current
            or status.get("layout") != library_index.runtime_names(existing, labels))


# Where each phase sits on one 0..1 bar. Measured on the owner's 20 roots: reading the files
# dominates a cold build (~30 s of ~40), classifying ~10 s, writing the asset files ~5 s. An
# incremental build skips "reading", so its bar starts at the classifying band.
_PHASE_BANDS = {"reading": (0.0, 0.55), "classifying": (0.55, 0.85), "assets": (0.85, 1.0)}
PHASE_LABELS = {"reading": "reading files", "classifying": "classifying packages",
                "assets": "writing asset files"}


def progress_fraction(phase: str, done: int, total: int) -> float:
    low, high = _PHASE_BANDS.get(phase, (0.0, 1.0))
    return low + (high - low) * (done / total if total else 1.0)


def report_lines(manifest: dict, result: dict, seconds: float, registered: str) -> list:
    """The text of the "Poser Library Index Report" block."""
    stats = manifest["stats"]
    kinds = stats["kinds"]
    lines = [
        f"Library index built in {seconds:.1f}s: {stats['shown']} package(s) in {stats['folders']} folder(s).",
        f"Asset files: {result['written']} written, {result['unchanged']} unchanged, {result['removed']} removed.",
        f"Library: {registered}",
        "",
        f"Entries scanned: {stats['entries']} ({stats['reused']} reused from the last build, "
        f"{stats['classified']} classified).",
        "  shown:  " + ", ".join(f"{k} {kinds.get(k, 0)}" for k in library_index.SHOWN_KINDS),
        "  hidden: " + ", ".join(f"{k} {n}" for k, n in sorted(kinds.items())
                                  if k not in library_index.SHOWN_KINDS),
        "Thumbnails: " + ", ".join(f"{k} {n}" for k, n in sorted(stats["thumbnails"].items())),
    ]
    child = result.get("child") or {}
    if child:
        lines.append(f"Child Blender {child.get('blender', '?')}: {child.get('assets', 0)} asset(s), "
                     f"{child.get('placeholders', 0)} placeholder(s), {child.get('failed', 0)} unreadable thumbnail(s).")
    incomplete = [e for f in manifest["folders"] for e in f["entries"] if e["incomplete"]]
    if incomplete:
        lines += ["", f"Incomplete packages ({len(incomplete)}; shown, tagged 'incomplete'):"]
        lines += [f"  {e['description']}" for e in incomplete[:50]]
    return lines


# ---------------------------------------------------------------------------
# Planning
# ---------------------------------------------------------------------------

def plan_asset_build(manifest: dict, previous, out_dir: str) -> dict:
    """What a build must do to bring `out_dir` in line with `manifest`.

    {"write": [folder dicts], "remove": [file names], "unchanged": n}. Beyond what
    changed since `previous`, a folder whose asset file is missing from disk is written
    again (the user may have deleted it), and any `.blend` in the directory that no folder
    owns is removed, including a `.tmp-` file an interrupted build left behind.
    """
    change = library_index.changed_folders(previous, manifest)
    write = {f["blend"]: f for f in change["write"]}
    for folder in manifest["folders"]:
        if not os.path.isfile(os.path.join(out_dir, folder["blend"])):
            write.setdefault(folder["blend"], folder)

    owned = {f["blend"] for f in manifest["folders"]}
    remove = set(change["remove"])
    try:
        on_disk = os.listdir(out_dir)
    except OSError:
        on_disk = []
    for name in on_disk:
        if name.endswith(".blend") and name not in owned:
            remove.add(name)
    remove = sorted(n for n in remove if n not in owned)
    ordered = [f for f in manifest["folders"] if f["blend"] in write]
    return {"write": ordered, "remove": remove, "unchanged": len(manifest["folders"]) - len(ordered)}


# ---------------------------------------------------------------------------
# The child process
# ---------------------------------------------------------------------------

def blender_command(binary: str, job_path: str) -> list:
    """The command line that runs the child script against a job file.

    --factory-startup: the child needs no user preferences or add-ons, and starts faster.
    --python-exit-code 1: an uncaught exception must fail the build, not exit 0.
    """
    return [binary, "--background", "--factory-startup", "--python-exit-code", "1",
            "--python", _CHILD_SCRIPT, "--", job_path]


def run_child(command: list, progress=None, cancelled=None, tail_lines: int = 25) -> dict:
    """Run `command`, read the child's protocol lines, return how it went.

    {"ok", "returncode", "summary" (the POSER_DONE json or {}), "error", "log"}.
    Output is read on a thread so `cancelled()` is still checked while the child is silent;
    cancelling terminates it (then kills it if it ignores that) and sets ok False with
    error "cancelled". `progress(done, total)` follows POSER_PROGRESS lines.
    """
    try:
        # UTF-8 explicitly: Blender prints it, and the locale default (cp1252 on Windows)
        # would garble or reject the non-ASCII folder names real libraries contain.
        proc = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                text=True, encoding="utf-8", errors="replace", bufsize=1)
    except OSError as exc:
        return {"ok": False, "returncode": -1, "summary": {}, "log": [],
                "error": f"couldn't start {command[0]!r}: {exc}"}
    lines = queue.Queue()

    def pump():
        try:
            for line in proc.stdout:
                lines.put(line.rstrip("\r\n"))
        except ValueError:
            pass    # the pipe was closed under us after a cancel
        lines.put(None)

    threading.Thread(target=pump, daemon=True).start()

    log, summary, error = [], {}, ""
    was_cancelled = False
    finished = False
    while not finished:
        try:
            line = lines.get(timeout=0.2)
        except queue.Empty:
            line = ""
        if line is None:
            finished = True
        elif line:
            log.append(line)
            del log[:-tail_lines]
            if line.startswith("POSER_PROGRESS ") and progress is not None:
                try:
                    done, total = (int(x) for x in line.split()[1:3])
                except ValueError:
                    pass
                else:
                    progress(done, total)
            elif line.startswith("POSER_DONE "):
                try:
                    summary = json.loads(line[len("POSER_DONE "):])
                except ValueError:
                    pass
            elif line.startswith("POSER_ERROR "):
                error = line[len("POSER_ERROR "):]
        if not finished and cancelled is not None and cancelled():
            was_cancelled = True
            proc.terminate()
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()
            break
    returncode = proc.wait()
    proc.stdout.close()
    if was_cancelled:
        error = "cancelled"
    elif returncode != 0 and not error:
        error = f"the child process exited with status {returncode}"
    elif returncode == 0 and not summary:
        error = "the child process finished without reporting a result"
    return {"ok": returncode == 0 and bool(summary) and not was_cancelled, "returncode": returncode,
            "summary": summary, "error": error, "log": log}


def build_assets(manifest: dict, previous, out_dir: str, command_for, progress=None, cancelled=None) -> dict:
    """Write the asset files for `manifest` into `out_dir`.

    `command_for(job_path)` returns the command to run (blender_command() bound to a
    binary, or a stand-in in tests). The manifest is saved *last*, only once the files are
    in place, so a failed or cancelled build is simply redone next time; `previous` stays
    the baseline for what changed. Raises AssetBuildError on failure and
    library_index.BuildCancelled on cancel. Returns {"written", "removed", "unchanged",
    "child": the child's summary}.
    """
    os.makedirs(out_dir, exist_ok=True)
    plan = plan_asset_build(manifest, previous, out_dir)
    ids = library_index.catalog_ids(manifest)
    summary = {}

    if plan["write"]:
        job_path = os.path.join(out_dir, _JOB_FILE)
        with open(job_path, "w", encoding="utf-8") as fh:
            json.dump({"out_dir": out_dir, "folders": plan["write"], "catalog_ids": ids}, fh)
        try:
            result = run_child(command_for(job_path), progress=progress, cancelled=cancelled)
        finally:
            try:
                os.remove(job_path)
            except OSError:
                pass
        if result["error"] == "cancelled":
            raise library_index.BuildCancelled()
        if not result["ok"]:
            raise AssetBuildError(f"{result['error']}\n" + "\n".join(result["log"][-10:]))
        summary = result["summary"]

    for name in plan["remove"]:
        try:
            os.remove(os.path.join(out_dir, name))
        except OSError:
            pass
    with open(os.path.join(out_dir, CATALOG_FILE), "w", encoding="utf-8") as fh:
        fh.write(library_index.catalog_file_text(ids))
    library_index.save_manifest(manifest_path(out_dir), manifest)
    return {"written": len(plan["write"]), "removed": len(plan["remove"]),
            "unchanged": plan["unchanged"], "child": summary}


# ---------------------------------------------------------------------------
# Registering the folder as an asset library
# ---------------------------------------------------------------------------

def library_name(name: str) -> str:
    """The name to register: the user's, trimmed, or the default when it is blank."""
    return (name or "").strip() or DEFAULT_LIBRARY_NAME


_OUR_FOLDER_TAIL = os.path.join("poser_tools", "library")


def find_asset_library(libraries, directory: str):
    """This add-on's entry in `libraries` (preferences.filepaths.asset_libraries), or None.

    Found by folder, never by name: the user can rename it, and Blender renames a clashing name
    to `Name.001` on its own. First the entry for `directory`; failing that, one pointing at a
    `poser_tools/library` folder, which only this add-on creates (library_dir()): preferences
    carried over from another Blender version still point at that version's folder.
    """
    wanted = os.path.normpath(directory)
    for library in libraries:
        if os.path.normpath(library.path) == wanted:
            return library
    for library in libraries:
        if os.path.normpath(library.path).endswith(os.sep + _OUR_FOLDER_TAIL):
            return library
    return None


def ensure_asset_library(libraries, name: str, directory: str) -> tuple:
    """Make sure this add-on's library is registered at `directory` under `name`.

    Returns (library, "created" | "renamed" | "updated" | "unchanged"). Idempotent: an existing
    entry (find_asset_library) is reused, repointed and renamed, since `libraries.new()` with a
    taken name silently makes `Name.001` (probed on 5.3.0 and 5.0.1). A name another library already
    has comes back from Blender as `Name.001`; that is accepted as done, not retried every build.
    `enabled` exists from 5.3 and is switched back on if the user turned it off.
    """
    name = library_name(name)
    library = find_asset_library(libraries, directory)
    if library is None:
        return libraries.new(name=name, directory=directory), "created"
    action = "unchanged"
    if os.path.normpath(library.path) != os.path.normpath(directory):
        library.path = directory
        action = "updated"
    if library.name != name and not re.fullmatch(re.escape(name) + r"\.\d{3}", library.name):
        library.name = name
        action = "renamed" if action == "unchanged" else action
    if hasattr(library, "enabled") and not library.enabled:
        library.enabled = True
        action = "updated"
    return library, action


def remove_asset_library(libraries, directory: str):
    """Unregister this add-on's library; returns the name it had, or None if there was none."""
    library = find_asset_library(libraries, directory)
    if library is None:
        return None
    name = library.name
    libraries.remove(library)
    return name


# ---------------------------------------------------------------------------
# From a library asset back to its package (7.4: Asset Browser and shelf clicks)
# ---------------------------------------------------------------------------

# "material" is a MAT pose or a material collection; "slot_material" a single material (8.4).
PACKAGE_KINDS = ("injection", "removal", "material", "slot_material")
_INCOMPLETE_PREFIX_RE = re.compile(r'^\[INCOMPLETE:[^\]]*\]\s*')


def package_kind(fields: dict, tags) -> str:
    """One of PACKAGE_KINDS for one of our assets, "" for any other asset.

    `fields` are the asset's custom metadata (poser_kind etc.), `tags` its tag names. The
    tags are the fallback because they are Blender's own metadata and always survive.
    """
    kind = fields.get("poser_kind") or ""
    if kind in PACKAGE_KINDS:
        return kind
    tags = set(tags)
    if "poser" in tags:
        for candidate in PACKAGE_KINDS:
            if candidate in tags:
                return candidate
    return ""


def resolve_package_asset(fields: dict, description: str, tags, roots) -> tuple:
    """(path, kind, problem) for the package a library asset stands for.

    `problem` is "" when `path` is an existing file, else a message for the user. The asset
    records its Runtime root and the path under it (library_assets_child.make_standin); the
    description holds the same relative path, so it stands in if the custom keys are ever
    missing. Read live on Blender 5.3 the keys are there (docs/handoff-morph-injection.md, 7.4).

    The recorded root is tried first, then every configured root: a Runtime folder that was
    moved or remounted elsewhere still finds the package without rebuilding the index.
    """
    kind = package_kind(fields, tags)
    if not kind:
        return "", "", "This asset isn't a Poser morph package."
    rel_path = fields.get("poser_rel_path") or _INCOMPLETE_PREFIX_RE.sub("", description or "").strip()
    if not rel_path:
        return "", kind, "This asset doesn't record which package it stands for. Rebuild the library index."

    candidates = []
    for root in [fields.get("poser_root") or "", *roots]:
        if root and root not in candidates:
            candidates.append(root)
    for root in candidates:
        path = os.path.join(root, *rel_path.split("/"))
        if os.path.isfile(path):
            return path, kind, ""
    where = candidates[0] if candidates else "any Runtime folder"
    return "", kind, (
        f"Package not found: {rel_path} (looked in {where}"
        f"{f' and {len(candidates) - 1} other Runtime folder(s)' if len(candidates) > 1 else ''}). "
        "Is the drive mounted? If the file was moved, Update the library index in Preferences."
    )
