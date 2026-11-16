# SPDX-License-Identifier: GPL-3.0-or-later
"""Tests for core/library_build.py (Phase 7.3b): planning, process control, registration.

The Blender child process itself isn't run here: a stand-in script speaks the same protocol
(POSER_PROGRESS / POSER_DONE / POSER_ERROR), so what is tested is the plumbing around it.
The real child is verified headless on Blender, see docs/handoff-morph-injection.md.
"""

import json
import os
import sys
import textwrap
import time

import pytest

from core import library_build as lb
from core.cr2 import library_index as li
from synthetic_library import library, lib  # noqa: F401  (fixtures)

FAKE_CHILD = textwrap.dedent('''
    import json, os, sys, time
    job = json.load(open(sys.argv[1]))
    mode = os.environ.get("FAKE_CHILD_MODE", "ok")
    if mode == "hang":
        print("POSER_PROGRESS 0 1", flush=True)
        time.sleep(60)
    if mode == "crash":
        print("some noise"); sys.exit(3)
    if mode == "error":
        print("POSER_ERROR ValueError: boom", flush=True); sys.exit(1)
    if mode == "silent":
        sys.exit(0)
    for i, folder in enumerate(job["folders"], 1):
        open(os.path.join(job["out_dir"], folder["blend"]), "w").write("fake " + folder["hash"])
        print("POSER_PROGRESS", i, len(job["folders"]), flush=True)
    print("POSER_PROGRESS not numbers", flush=True)
    print("POSER_DONE " + json.dumps({"folders": len(job["folders"]), "assets": 7}), flush=True)
''')


@pytest.fixture
def child(tmp_path, monkeypatch):
    script = tmp_path / "fake_child.py"
    script.write_text(FAKE_CHILD)
    calls = []

    def command_for(job_path):
        calls.append(job_path)
        return [sys.executable, str(script), job_path]

    command_for.calls = calls
    return command_for


@pytest.fixture
def manifest(library):  # noqa: F811
    return li.build_manifest([library.path])


def _out(tmp_path):
    return str(tmp_path / "out")


# ---- planning ------------------------------------------------------------------------------

def test_a_fresh_plan_writes_every_folder(manifest, tmp_path):
    plan = lb.plan_asset_build(manifest, None, _out(tmp_path))
    assert [f["blend"] for f in plan["write"]] == [f["blend"] for f in manifest["folders"]]
    assert plan["remove"] == [] and plan["unchanged"] == 0


def test_an_up_to_date_library_needs_nothing(manifest, tmp_path):
    out = tmp_path / "out"
    out.mkdir()
    for f in manifest["folders"]:
        (out / f["blend"]).write_text("x")
    assert lb.plan_asset_build(manifest, manifest, str(out)) == {"write": [], "remove": [], "unchanged": 3}


def test_a_deleted_asset_file_is_written_again_and_strays_are_swept(manifest, tmp_path):
    out = tmp_path / "out"
    out.mkdir()
    for f in manifest["folders"]:
        (out / f["blend"]).write_text("x")
    gone = manifest["folders"][0]["blend"]
    os.remove(out / gone)
    (out / "old-12345678.blend").write_text("x")
    (out / ".tmp-half-written.blend").write_text("x")
    (out / "notes.txt").write_text("keep")
    plan = lb.plan_asset_build(manifest, manifest, str(out))
    assert [f["blend"] for f in plan["write"]] == [gone]
    assert plan["remove"] == [".tmp-half-written.blend", "old-12345678.blend"]


# ---- the child process ---------------------------------------------------------------------

def test_blender_command_runs_the_child_script_and_fails_on_exceptions():
    cmd = lb.blender_command("/usr/bin/blender", "/tmp/job.json")
    assert cmd[0] == "/usr/bin/blender" and cmd[-2:] == ["--", "/tmp/job.json"]
    assert "--background" in cmd and "--factory-startup" in cmd
    assert cmd[cmd.index("--python-exit-code") + 1] == "1"
    assert os.path.isfile(cmd[cmd.index("--python") + 1])


def test_run_child_reports_progress_and_the_summary(tmp_path, child):
    job = tmp_path / "job.json"
    job.write_text(json.dumps({"out_dir": str(tmp_path), "folders": [{"blend": "a.blend", "hash": "h"}] * 2}))
    seen = []
    result = lb.run_child(child(str(job)), progress=lambda d, t: seen.append((d, t)))
    assert result["ok"] and result["summary"] == {"folders": 2, "assets": 7}
    assert seen == [(1, 2), (2, 2)]                 # the malformed progress line is ignored


@pytest.mark.parametrize("mode, text", [
    ("error", "ValueError: boom"), ("crash", "status 3"), ("silent", "without reporting"),
])
def test_run_child_failures_say_why(tmp_path, child, monkeypatch, mode, text):
    monkeypatch.setenv("FAKE_CHILD_MODE", mode)
    job = tmp_path / "job.json"
    job.write_text(json.dumps({"out_dir": str(tmp_path), "folders": []}))
    result = lb.run_child(child(str(job)))
    assert not result["ok"] and text in result["error"]


def test_run_child_cancel_stops_a_silent_child_promptly(tmp_path, child, monkeypatch):
    monkeypatch.setenv("FAKE_CHILD_MODE", "hang")
    job = tmp_path / "job.json"
    job.write_text(json.dumps({"out_dir": str(tmp_path), "folders": []}))
    started = time.time()
    result = lb.run_child(child(str(job)), cancelled=lambda: time.time() - started > 0.5)
    assert result["error"] == "cancelled" and not result["ok"] and time.time() - started < 10


def test_run_child_cannot_start_a_missing_binary():
    result = lb.run_child(["/definitely/not/blender"])
    assert not result["ok"] and "couldn't start" in result["error"]


# ---- build_assets --------------------------------------------------------------------------

def test_a_build_writes_assets_catalogs_and_the_manifest_last(manifest, tmp_path, child):
    out = _out(tmp_path)
    result = lb.build_assets(manifest, None, out, child)
    assert (result["written"], result["removed"], result["unchanged"]) == (3, 0, 0)
    assert result["child"]["assets"] == 7
    assert sorted(f for f in os.listdir(out) if f.endswith(".blend")) == sorted(f["blend"] for f in manifest["folders"])
    assert ".job.json" not in os.listdir(out)
    cats = open(os.path.join(out, lb.CATALOG_FILE)).read()
    assert "VERSION 1" in cats and ":pose/Fig INJ:Fig INJ" in cats and ":pose:pose" in cats
    assert li.load_manifest(lb.manifest_path(out))["folders"] == manifest["folders"]


def test_a_second_build_does_not_start_the_child(manifest, tmp_path, child):
    out = _out(tmp_path)
    lb.build_assets(manifest, None, out, child)
    saved = li.load_manifest(lb.manifest_path(out))
    child.calls.clear()
    result = lb.build_assets(manifest, saved, out, child)
    assert (result["written"], result["unchanged"]) == (0, 3) and child.calls == []


def test_only_a_changed_folder_is_rewritten_and_a_vanished_one_is_removed(library, tmp_path, child):  # noqa: F811
    out = _out(tmp_path)
    first = li.build_manifest([library.path])
    lb.build_assets(first, None, out, child)
    library.write("pose/Fig INJ/Brow.png", "new")
    os.utime(os.path.join(library.path, "Runtime", "libraries", "pose", "Fig INJ", "Brow.png"), (5, 5))
    os.remove(os.path.join(library.path, "Runtime", "libraries", "pose", "Fig REM", "Brow.pz2"))
    second = li.build_manifest([library.path], previous=first)
    result = lb.build_assets(second, first, out, child)
    assert (result["written"], result["removed"]) == (1, 1)
    assert sorted(f for f in os.listdir(out) if f.endswith(".blend")) == sorted(f["blend"] for f in second["folders"])


@pytest.mark.parametrize("mode", ["error", "crash"])
def test_a_failed_build_raises_and_saves_nothing(manifest, tmp_path, child, monkeypatch, mode):
    monkeypatch.setenv("FAKE_CHILD_MODE", mode)
    out = _out(tmp_path)
    with pytest.raises(lb.AssetBuildError):
        lb.build_assets(manifest, None, out, child)
    assert not os.path.exists(lb.manifest_path(out)) and ".job.json" not in os.listdir(out)


def test_a_cancelled_build_raises_cancelled_and_saves_nothing(manifest, tmp_path, child, monkeypatch):
    monkeypatch.setenv("FAKE_CHILD_MODE", "hang")
    out = _out(tmp_path)
    started = time.time()
    with pytest.raises(li.BuildCancelled):
        lb.build_assets(manifest, None, out, child, cancelled=lambda: time.time() - started > 0.5)
    assert not os.path.exists(lb.manifest_path(out))


# ---- registration --------------------------------------------------------------------------

class _Lib:
    def __init__(self, name, path, enabled=None):
        self.name, self.path = name, path
        if enabled is not None:
            self.enabled = enabled


class _Libraries(list):
    def new(self, *, name, directory):
        # Blender silently renames a duplicate, which is exactly what ensure_asset_library avoids.
        taken = {lib.name for lib in self}
        lib = _Lib(name if name not in taken else name + ".001", directory)
        self.append(lib)
        return lib


def test_registering_is_idempotent_and_corrects_the_path():
    libs = _Libraries([_Lib("User Library", "/home/u/Assets")])
    lib, action = lb.ensure_asset_library(libs, lb.LIBRARY_NAME, "/cfg/library")
    assert (action, lib.path, len(libs)) == ("created", "/cfg/library", 2)
    again, action = lb.ensure_asset_library(libs, lb.LIBRARY_NAME, "/cfg/library/")
    assert (action, again is lib, len(libs)) == ("unchanged", True, 2)      # a trailing slash is not a change
    moved, action = lb.ensure_asset_library(libs, lb.LIBRARY_NAME, "/elsewhere")
    assert (action, moved.path, len(libs)) == ("updated", "/elsewhere", 2)
    assert [l.name for l in libs] == ["User Library", lb.LIBRARY_NAME]      # never a "Name.001"


def test_a_disabled_library_is_switched_back_on_where_that_exists():
    libs = _Libraries([_Lib(lb.LIBRARY_NAME, "/cfg/library", enabled=False)])
    lib, action = lb.ensure_asset_library(libs, lb.LIBRARY_NAME, "/cfg/library")
    assert (action, lib.enabled) == ("updated", True)


def test_removing_the_library_only_touches_its_own_entry():
    libs = _Libraries([_Lib("User Library", "/a"), _Lib(lb.LIBRARY_NAME, "/b")])
    libs.remove = lambda lib: list.remove(libs, lib)
    assert lb.remove_asset_library(libs, lb.LIBRARY_NAME) is True
    assert [l.name for l in libs] == ["User Library"]
    assert lb.remove_asset_library(libs, lb.LIBRARY_NAME) is False
