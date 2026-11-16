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
import subprocess
import threading

from .cr2 import library_index

LIBRARY_NAME = "Poser Morph Packages"
MANIFEST_FILE = "poser_index.json.gz"
CATALOG_FILE = "blender_assets.cats.txt"
_JOB_FILE = ".job.json"
_CHILD_SCRIPT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "library_assets_child.py")


class AssetBuildError(Exception):
    """The child Blender process failed; the previous index, if any, is left as it was."""


def library_dir() -> str:
    """The directory holding the asset files, catalog and manifest (created if needed).

    The same per-add-on config folder runtime_index already keeps its cache in, which is
    per Blender version. bpy is imported here, not at module level, so the rest of this
    module stays importable without Blender.
    """
    import bpy  # noqa: PLC0415
    path = bpy.utils.user_resource("CONFIG", path=os.path.join("poser_tools", "library"))
    os.makedirs(path, exist_ok=True)
    return path


def manifest_path(out_dir: str) -> str:
    return os.path.join(out_dir, MANIFEST_FILE)


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

def ensure_asset_library(libraries, name: str, directory: str) -> tuple:
    """Make sure `libraries` (preferences.filepaths.asset_libraries) has `name` pointing at `directory`.

    Returns (library, "created" | "updated" | "unchanged"). Idempotent: calling
    `libraries.new()` twice with one name silently makes `Name.001` (probed on Blender 5.3.0
    and 5.0.1), so an existing entry is reused and only its path is corrected. `enabled`
    exists from 5.3 and is switched back on if the user turned it off.
    """
    for library in libraries:
        if library.name == name:
            action = "unchanged"
            if os.path.normpath(library.path) != os.path.normpath(directory):
                library.path = directory
                action = "updated"
            if hasattr(library, "enabled") and not library.enabled:
                library.enabled = True
                action = "updated"
            return library, action
    return libraries.new(name=name, directory=directory), "created"


def remove_asset_library(libraries, name: str) -> bool:
    """Remove the registered library called `name`; False if there was none."""
    for library in list(libraries):
        if library.name == name:
            libraries.remove(library)
            return True
    return False
