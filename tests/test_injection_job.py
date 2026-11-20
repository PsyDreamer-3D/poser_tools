# SPDX-License-Identifier: GPL-3.0-or-later
"""Tests for core/cr2/injection_job.py: Apply's package parse, correspondence and positions,
run as one bpy-free job (Phase 7.7), and for cr2_parser.read_figure_res_file().

A small synthetic figure (a point cloud OBJ with one `head` group, its mesh being the same
points) and a one-file package stand in for a real figure, so every stage runs for real.
"""

import queue

import numpy as np
import pytest

from core.cr2.correspondence_cache import pack_correspondence
from core.cr2.cr2_parser import CR2Parser, read_figure_res_file
from core.cr2.injection_job import InjectionJob
from core.cr2.injection_package import load_injection_package


def _jcm(name):
    return name.startswith("JCM")


def _cloud(n=300, seed=0):
    rng = np.random.default_rng(seed)
    return rng.normal(size=(n, 3)) * np.array([1.0, 2.0, 4.0])


def _obj(path, points):
    lines = [f"v {x:.6f} {y:.6f} {z:.6f}" for x, y, z in points]
    lines.append("g head")
    lines += [f"f {i + 1} {i + 2} {i + 3}" for i in range(0, len(points) - 2, 3)]
    path.write_text("\n".join(lines) + "\n")


def _channel(internal, label, deltas):
    d = "".join(f"\t\t\t\t\td {i} 0 0 0.5\n" for i in deltas)
    return (f"\t\t\ttargetGeom {internal}\n\t\t\t\t{{\n\t\t\t\tname {label}\n"
            f"\t\t\t\tindexes {len(deltas)}\n\t\t\t\tnumbDeltas 300\n"
            f"\t\t\t\tdeltas\n\t\t\t\t\t{{\n{d}\t\t\t\t\t}}\n\t\t\t\t}}\n")


def _package(path, *channels):
    path.write_text("{\n\tversion\n\t\t{\n\t\tnumber 4.01\n\t\t}\n\tactor head:1\n\t\t{\n\t\tchannels\n"
                    "\t\t\t{\n" + "".join(channels) + "\t\t\t}\n\t\t}\n}\n")


@pytest.fixture
def figure(tmp_path):
    points = _cloud()
    _obj(tmp_path / "fig.obj", points)
    _package(tmp_path / "Pkg.pz2",
             _channel("PBM_A", "MorphA", [0, 1, 2]),
             _channel("PBM_B", "MorphB", [5]),
             _channel("PBM_J", "JCM Knee", [7]),
             _channel("PBM_P", "Present", [8]),
             _channel("PBM_E", "Empty", []))
    return {"obj": str(tmp_path / "fig.obj"), "pkg": str(tmp_path / "Pkg.pz2"), "basis": points.copy()}


def _job(figure, **kwargs):
    kwargs.setdefault("existing_keys", ["Basis", "Present"])
    return InjectionJob(figure["pkg"], figure["obj"], figure["basis"], skip_name=_jcm, **kwargs)


def test_every_stage_runs_and_skips_are_sorted_out(figure):
    job = _job(figure)
    job.prepare()
    assert job.morph_names == ["PBM_A", "PBM_B", "PBM_J", "PBM_P", "PBM_E"]
    assert job.built_seconds is not None and not job.reused

    items = list(job.morphs())
    assert [(i, n) for i, n, _ in items] == [("PBM_A", "MorphA"), ("PBM_B", "MorphB")]
    assert job.jcm_skipped == ["JCM Knee"]
    assert job.already_present == ["Present"]
    assert job.empty_skipped == ["Empty"]
    moved = items[0][2]["positions"] - figure["basis"]
    assert np.count_nonzero(np.abs(moved).sum(axis=1)) == 3
    assert job.stage == "morphs" and job.progress == 1.0


def test_a_cached_correspondence_is_reused_and_a_stale_one_rebuilt(figure):
    first = _job(figure)
    first.prepare()
    blob = pack_correspondence(first.correspondence)

    reused = _job(figure, cached=(first.correspondence_key, blob))
    reused.prepare()
    assert reused.reused and reused.built_seconds is None and reused.rebuild_reason is None

    stale = _job(figure, cached=("another mesh", blob))
    stale.prepare()
    assert not stale.reused and stale.built_seconds is not None
    assert "changed" in stale.rebuild_reason

    broken = _job(figure, cached=(first.correspondence_key, b"not an npz"))
    broken.prepare()
    assert not broken.reused and "unusable" in broken.rebuild_reason


def test_a_selection_is_applied_and_its_unknown_names_kept(figure):
    job = _job(figure, selected=["PBM_B", "PBM_GONE"])
    job.prepare()
    assert job.morph_names == ["PBM_B"]
    assert job.missing_selected == ["PBM_GONE"]


def test_nothing_to_apply_stops_before_the_correspondence(figure):
    job = _job(figure, selected=["PBM_GONE"])
    job.prepare()
    assert job.morph_names == [] and job.correspondence is None and job.stage == "reading"


def test_run_hands_morphs_over_through_the_queue(figure):
    job = _job(figure)
    out = queue.Queue(maxsize=1)
    import threading
    worker = threading.Thread(target=job.run, args=(out,))
    worker.start()
    got = [out.get(timeout=30)[0], out.get(timeout=30)[0]]
    worker.join(timeout=30)
    assert got == ["PBM_A", "PBM_B"] and job.error is None and out.empty()


def test_run_stops_quietly_when_cancelled_and_keeps_real_errors(figure, tmp_path):
    job = _job(figure)
    job.cancelled.set()
    out = queue.Queue()
    job.run(out)
    assert out.empty() and job.error is None and job.stage == "reading"

    missing_obj = InjectionJob(figure["pkg"], str(tmp_path / "nope.obj"), figure["basis"], [], _jcm)
    missing_obj.run(queue.Queue())
    assert isinstance(missing_obj.error, OSError) and missing_obj.stage == "correspondence"


def test_load_injection_package_reports_progress_per_file(figure):
    seen = []
    load_injection_package(figure["pkg"], progress_callback=seen.append)
    assert seen == [1.0]


# ---- read_figure_res_file ------------------------------------------------------------------------

def test_figure_res_file_is_the_last_root_level_one(tmp_path):
    cr2 = tmp_path / "Fig.cr2"
    cr2.write_text(
        "{\n"
        "version\n\t{\n\tnumber 4.01\n\t}\n"
        "figureResFile :Runtime:Geometries:First.obj   # a comment\n"
        "actor hip:1\n\t{\n\tname Odd {name}\n\tfigureResFile :Runtime:Inside:Actor.obj\n\t}\n"
        'figureResFile ":Runtime:Geometries:My Figure:Last.obj"\n'
        "actor hip:1\n\t{\n\tchannels\n\t\t{\n\t\t}\n\t}\n"
        "}\n")
    assert read_figure_res_file(str(cr2)) == ":Runtime:Geometries:My Figure:Last.obj"
    assert read_figure_res_file(str(cr2)) == CR2Parser.parse_file(str(cr2)).geom_file


def test_figure_res_file_absent_is_empty(tmp_path):
    cr2 = tmp_path / "Prop.cr2"
    cr2.write_text("{\nversion\n\t{\n\tnumber 4\n\t}\n}\n")
    assert read_figure_res_file(str(cr2)) == ""


@pytest.mark.parametrize("name", ["!Aiko 3.cr2", "LaFemme Pro tmp.cr2", "Aiko3 All SP Morphs.cr2"])
def test_figure_res_file_agrees_with_the_parser_on_real_figures(asset_path, name):
    path = str(asset_path(name))
    assert read_figure_res_file(path) == CR2Parser.parse_file(path).geom_file
