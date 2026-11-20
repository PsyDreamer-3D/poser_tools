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
    assert "VERSION 1" in cats and ":Content/Pose/Fig INJ:Fig INJ" in cats and ":Content:Content" in cats
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


OURS = "/cfg/5.3/config/poser_tools/library"


def test_registering_is_idempotent_and_finds_our_entry_by_folder():
    libs = _Libraries([_Lib("User Library", "/home/u/Assets")])
    lib, action = lb.ensure_asset_library(libs, "", OURS)
    assert (action, lib.name, lib.path, len(libs)) == ("created", "Poser", OURS, 2)   # blank -> the default
    again, action = lb.ensure_asset_library(libs, "Poser", OURS + "/")
    assert (action, again is lib, len(libs)) == ("unchanged", True, 2)               # a trailing slash is not a change


def test_the_old_morph_packages_entry_is_renamed_in_place_not_duplicated():
    libs = _Libraries([_Lib("Poser Morph Packages", OURS)])
    lib, action = lb.ensure_asset_library(libs, "Poser", OURS)
    assert (action, [l.name for l in libs]) == ("renamed", ["Poser"])
    lib, action = lb.ensure_asset_library(libs, "My Poser Stuff", OURS)
    assert (action, lib.name, len(libs)) == ("renamed", "My Poser Stuff", 1)


def test_another_blender_versions_entry_is_repointed():
    # Preferences carried over from 5.2 still point at 5.2's folder.
    libs = _Libraries([_Lib("Poser", "/cfg/5.2/config/poser_tools/library")])
    lib, action = lb.ensure_asset_library(libs, "Poser", OURS)
    assert (action, lib.path, len(libs)) == ("updated", OURS, 1)


def test_someone_elses_library_with_our_name_is_left_alone():
    libs = _Libraries([_Lib("Poser", "/home/u/My Poser Renders")])
    lib, action = lb.ensure_asset_library(libs, "Poser", OURS)
    assert (action, lib.name, lib.path) == ("created", "Poser.001", OURS)
    assert libs[0].path == "/home/u/My Poser Renders"
    # Blender's `.001` is accepted, not fought over on every build.
    assert lb.ensure_asset_library(libs, "Poser", OURS)[1] == "unchanged"


def test_a_disabled_library_is_switched_back_on_where_that_exists():
    libs = _Libraries([_Lib("Poser", OURS, enabled=False)])
    lib, action = lb.ensure_asset_library(libs, "Poser", OURS)
    assert (action, lib.enabled) == ("updated", True)


def test_removing_the_library_only_touches_its_own_entry():
    libs = _Libraries([_Lib("User Library", "/a"), _Lib("Renamed By User", OURS)])
    libs.remove = lambda lib: list.remove(libs, lib)
    assert lb.remove_asset_library(libs, OURS) == "Renamed By User"
    assert [l.name for l in libs] == ["User Library"]
    assert lb.remove_asset_library(libs, OURS) is None


# ---- status, staleness, progress, report ---------------------------------------------------

def test_status_round_trips_and_is_cached_on_the_file(manifest, tmp_path):
    out = str(tmp_path)
    assert lb.read_status(out) is None
    lb.write_status(out, manifest, 12.34, now=1_700_000_000.0)
    status = lb.read_status(out)
    assert status["built_at"] == 1_700_000_000.0 and status["seconds"] == 12.3
    assert status["stats"]["shown"] == 4 and lb.read_status(out) is status          # cached, not re-read
    lb.write_status(out, manifest, 1.0, now=5.0)
    os.utime(os.path.join(out, lb.STATUS_FILE), ns=(1, 1))                            # a new mtime invalidates it
    assert lb.read_status(out)["built_at"] == 5.0


def test_a_damaged_status_file_reads_as_none(tmp_path):
    (tmp_path / lb.STATUS_FILE).write_text("{not json")
    assert lb.read_status(str(tmp_path)) is None
    (tmp_path / lb.STATUS_FILE).write_text('{"built_at": 1}')
    assert lb.read_status(str(tmp_path / "")) is None


def test_status_goes_stale_when_roots_or_blender_change(library, tmp_path):
    version = "5.3.0"
    m = li.build_manifest([library.path], signature_extra=li.asset_signature(version))
    lb.write_status(str(tmp_path), m, 1.0)
    status = lb.read_status(str(tmp_path))
    assert not lb.status_is_stale(status, [library.path], version)
    assert not lb.status_is_stale(status, [library.path, library.path, "/not/a/dir", ""], version)   # same effective roots
    assert lb.status_is_stale(status, [library.path, str(tmp_path)], version)
    assert lb.status_is_stale(status, [library.path], "5.0.1")
    assert lb.status_is_stale(status, [], version)
    assert lb.status_is_stale(status, [library.path], version, {library.path: "Renamed"})
    assert not lb.status_is_stale(status, [library.path], version, {library.path: ""})   # blank = folder name
    old = {k: v for k, v in status.items() if k != "layout"}          # built before Runtime catalogs
    assert lb.status_is_stale(old, [library.path], version)


def test_progress_is_one_monotonic_bar_across_the_phases():
    marks = [lb.progress_fraction(p, d, 10) for p, d in
             [("reading", 0), ("reading", 10), ("classifying", 0), ("classifying", 10), ("assets", 0), ("assets", 10)]]
    assert marks == sorted(marks) and marks[0] == 0.0 and marks[-1] == pytest.approx(1.0)
    assert lb.progress_fraction("assets", 0, 0) == 1.0                  # an empty phase is complete, not a division error


def test_the_report_summarizes_the_build(manifest):
    lines = lb.report_lines(manifest, {"written": 3, "unchanged": 0, "removed": 1,
                                       "child": {"blender": "5.3.0", "assets": 4, "placeholders": 3, "failed": 0}},
                            9.87, "Poser Morph Packages (created)")
    text = "\n".join(lines)
    assert "4 package(s) in 3 folder(s)" in text and "3 written, 0 unchanged, 1 removed" in text
    assert "shown:  injection 3, removal 1" in text and "hidden: other 1, pose 2" in text
    assert "Incomplete packages (1" in text and "[INCOMPLETE: 1 of 2 files missing]" in text
    assert "created" in text and "Child Blender 5.3.0: 4 asset(s), 3 placeholder(s)" in text


# --- 7.4: from an asset back to its package -----------------------------------------------

def _package(tmp_path, rel="Runtime/libraries/pose/Pack/InjDeltas.Foo.pz2"):
    root = tmp_path / "Content"
    path = root.joinpath(*rel.split("/"))
    path.parent.mkdir(parents=True)
    path.write_text("{}")
    return str(root), rel, str(path)


def test_an_asset_resolves_through_its_own_keys(tmp_path):
    root, rel, path = _package(tmp_path)
    fields = {"poser_root": root, "poser_rel_path": rel, "poser_kind": "injection"}
    assert lb.resolve_package_asset(fields, rel, ["injection", "poser"], []) == (path, "injection", "")


def test_without_its_keys_an_asset_falls_back_to_tags_and_description(tmp_path):
    root, rel, path = _package(tmp_path)
    description = f"[INCOMPLETE: 1 of 3 files missing] {rel}"
    assert lb.resolve_package_asset({}, description, ["removal", "poser"], [root]) == (path, "removal", "")


def test_a_moved_root_is_found_among_the_configured_ones(tmp_path):
    root, rel, path = _package(tmp_path)
    fields = {"poser_root": str(tmp_path / "Old Mount"), "poser_rel_path": rel, "poser_kind": "injection"}
    assert lb.resolve_package_asset(fields, rel, [], ["/elsewhere", root])[0] == path


def test_a_missing_package_says_where_it_looked(tmp_path):
    fields = {"poser_root": str(tmp_path), "poser_rel_path": "Runtime/x.pz2", "poser_kind": "removal"}
    path, kind, problem = lb.resolve_package_asset(fields, "", [], [str(tmp_path / "b")])
    assert (path, kind) == ("", "removal")
    assert "Runtime/x.pz2" in problem and str(tmp_path) in problem and "1 other" in problem


def test_other_assets_are_not_packages():
    assert lb.package_kind({}, ["injection"]) == ""           # someone else's tag, no "poser"
    assert lb.package_kind({"poser_kind": "pose"}, []) == ""
    assert lb.resolve_package_asset({}, "Runtime/x.pz2", [], [])[2]


def test_a_mat_pose_asset_resolves_like_a_package(tmp_path):
    root, rel, path = _package(tmp_path, "Runtime/libraries/pose/Lady/! MAT Lady !.pz2")
    fields = {"poser_root": root, "poser_rel_path": rel, "poser_kind": "material"}
    assert lb.resolve_package_asset(fields, rel, ["material", "poser"], []) == (path, "material", "")
    assert lb.package_kind({}, ["material", "poser"]) == "material"


def test_a_single_material_asset_resolves_with_its_own_kind(tmp_path):
    root, rel, path = _package(tmp_path, "Runtime/libraries/Materials/Glass/Clear.mt5")
    fields = {"poser_root": root, "poser_rel_path": rel, "poser_kind": "slot_material"}
    assert lb.resolve_package_asset(fields, rel, [], []) == (path, "slot_material", "")
    assert lb.package_kind({}, ["slot_material", "poser"]) == "slot_material"
