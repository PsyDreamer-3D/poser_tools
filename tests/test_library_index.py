# SPDX-License-Identifier: GPL-3.0-or-later
"""Tests for core/cr2/library_index.py (Phase 7.3a): classification and the index manifest."""

import gzip
import os
import random
import textwrap

import pytest

from core.cr2 import library_index as li
from core.cr2.injection_package import resolve_package_files
from core.cr2.injection_scan import scan_channels
from core.cr2.poser_io import read_poser_file
from synthetic_library import (  # noqa: F401  (the fixtures are used by name)
    BONE_LEAF, HIDE_LEAF, HIDE_NAMED_LEAF, INJ_LEAF, OTHER_LEAF, PMD_LEAF, PMD_REMOVAL_LEAF, POSE_LEAF, REM_HIDDEN_LEAF,
    REM_LEAF, _orchestrator, lib, library,
)

_REAL_BASE = ("/run/media/jess-green/236a91c9-2598-40dd-b968-9c0f0be8a903/Artwork/Poser Content"
              "/Base Figures")


# ---- the leaf sniff ------------------------------------------------------------------------

@pytest.mark.parametrize("text, kind", [
    (INJ_LEAF, li.LEAF_INJECTION), (REM_LEAF, li.LEAF_REMOVAL), (HIDE_LEAF, li.LEAF_POSE),
    (POSE_LEAF, li.LEAF_POSE), (PMD_LEAF, li.LEAF_PMD), (OTHER_LEAF, li.LEAF_OTHER),
    (HIDE_NAMED_LEAF, li.LEAF_POSE), (REM_HIDDEN_LEAF, li.LEAF_REMOVAL), (PMD_REMOVAL_LEAF, li.LEAF_PMD),
])
def test_sniff_leaf(text, kind):
    assert li.sniff_leaf(text) == kind


def test_hide_files_are_not_removals():
    # A grep for `name -` called the valueParm flavour removals: 624 wrong tiles on the real
    # library. The targetGeom flavour (`name -` + `hidden 1`, no delta list) fooled the first
    # fix too: 154 more, found by looking at the library in the Asset Browser.
    assert li.sniff_leaf(HIDE_LEAF) != li.LEAF_REMOVAL
    assert li.sniff_leaf(HIDE_NAMED_LEAF) != li.LEAF_REMOVAL


def test_a_removal_that_also_says_hidden_is_still_a_removal():
    # Telling the two apart by `hidden` alone would have dropped a vendor's real RemDeltas files.
    assert li.sniff_leaf(REM_HIDDEN_LEAF) == li.LEAF_REMOVAL


def test_sniff_reads_only_the_head_of_a_huge_injection_leaf():
    deltas = "".join(f"\t\t\t\t\td {i} 0 0 1\n" for i in range(5000))
    huge = INJ_LEAF.replace("\t\t\t\t\td 0 0 0 1\n", deltas)
    assert len(huge) > li.HEAD_BYTES
    assert li.sniff_leaf(huge[:li.HEAD_BYTES]) == li.LEAF_INJECTION


# ---- classify_entry ------------------------------------------------------------------------

def _classify(lib, rel):
    return li.classify_entry(str(os.path.join(lib.path, "Runtime", "libraries", rel)), [lib.path])


def test_a_plain_leaf_is_its_own_entry(lib):
    lib.write("pose/Pack/Foo.pz2", INJ_LEAF)
    rec = _classify(lib, "pose/Pack/Foo.pz2")
    assert (rec["kind"], rec["n_files"], rec["incomplete"]) == ("injection", 1, False)


def test_an_orchestrator_is_classified_by_its_leaves_through_nested_orchestrators(lib):
    lib.write("!DAZ/Fig/Deltas/InjDeltas.A.pz2", INJ_LEAF)
    lib.write("!DAZ/Fig/Deltas/InjDeltas.B.pz2", INJ_LEAF)
    lib.write("pose/Pack/All.pz2", _orchestrator(":Runtime:libraries:!DAZ:Fig:Deltas:InjDeltas.A.pz2"))
    lib.write("pose/Pack/Top.pz2", _orchestrator(
        ":Runtime:libraries:pose:Pack:All.pz2", ":Runtime:libraries:!DAZ:Fig:Deltas:InjDeltas.B.pz2"))
    rec = _classify(lib, "pose/Pack/Top.pz2")
    assert (rec["kind"], rec["n_files"], rec["n_missing"]) == ("injection", 2, 0)


def test_targets_are_matched_ignoring_case(lib):
    lib.write("!DAZ/Fig/Deltas/InjDeltas.A.pz2", INJ_LEAF)
    lib.write("pose/Pack/All.pz2", _orchestrator(":Runtime:Libraries:!daz:FIG:deltas:injdeltas.a.pz2"))
    assert _classify(lib, "pose/Pack/All.pz2")["kind"] == "injection"


@pytest.mark.parametrize("leaf, kind", [
    (REM_LEAF, "removal"), (HIDE_LEAF, "pose"), (POSE_LEAF, "pose"), (PMD_LEAF, "pmd"), (OTHER_LEAF, "other"),
    (BONE_LEAF, "armature"),
    (HIDE_NAMED_LEAF, "pose"), (REM_HIDDEN_LEAF, "removal"), (PMD_REMOVAL_LEAF, "pmd"),
])
def test_entry_kinds(lib, leaf, kind):
    lib.write("!DAZ/Fig/Deltas/Leaf.pz2", leaf)
    lib.write("pose/Pack/E.pz2", _orchestrator(":Runtime:libraries:!DAZ:Fig:Deltas:Leaf.pz2"))
    assert _classify(lib, "pose/Pack/E.pz2")["kind"] == kind


def test_missing_targets_make_an_entry_incomplete_or_hide_it_when_nothing_resolves(lib):
    lib.write("!DAZ/Fig/Deltas/InjDeltas.A.pz2", INJ_LEAF)
    lib.write("pose/Pack/Half.pz2", _orchestrator(
        ":Runtime:libraries:!DAZ:Fig:Deltas:InjDeltas.A.pz2", ":Runtime:libraries:gone.pz2"))
    lib.write("pose/Pack/None.pz2", _orchestrator(":Runtime:libraries:gone.pz2", ":Runtime:libraries:gone2.pz2"))
    half = _classify(lib, "pose/Pack/Half.pz2")
    assert (half["kind"], half["incomplete"], half["n_missing"], half["n_files"]) == ("injection", True, 1, 1)
    assert _classify(lib, "pose/Pack/None.pz2")["kind"] == "missing"


def test_a_target_in_another_configured_root_resolves(tmp_path):
    a, b = tmp_path / "A", tmp_path / "B"
    leaf = b / "Runtime" / "libraries" / "!DAZ" / "Fig" / "Deltas" / "InjDeltas.A.pz2"
    leaf.parent.mkdir(parents=True)
    leaf.write_text(INJ_LEAF)
    entry = a / "Runtime" / "libraries" / "pose" / "E.pz2"
    entry.parent.mkdir(parents=True)
    entry.write_text(_orchestrator(":Runtime:libraries:!DAZ:Fig:Deltas:InjDeltas.A.pz2"))
    assert li.classify_entry(str(entry), [str(a)])["kind"] == "missing"
    assert li.classify_entry(str(entry), [str(a), str(b)])["kind"] == "injection"


def test_a_cycle_and_an_over_deep_tree_do_not_hang(lib):
    lib.write("pose/A.pz2", _orchestrator(":Runtime:libraries:pose:B.pz2"))
    lib.write("pose/B.pz2", _orchestrator(":Runtime:libraries:pose:A.pz2"))
    assert _classify(lib, "pose/A.pz2")["kind"] == "other"
    for i in range(li.MAX_DEPTH + 3):
        lib.write(f"pose/chain/c{i}.pz2", _orchestrator(f":Runtime:libraries:pose:chain:c{i + 1}.pz2"))
    lib.write(f"pose/chain/c{li.MAX_DEPTH + 3}.pz2", INJ_LEAF)
    assert _classify(lib, "pose/chain/c0.pz2")["incomplete"] is True


def test_compressed_entries_are_read(lib):
    lib.write("pose/Pack/Z.p2z", gzip.compress(INJ_LEAF.encode()), binary=True)
    assert _classify(lib, "pose/Pack/Z.p2z")["kind"] == "injection"
    lib.write("pose/Pack/Fake.p2z", INJ_LEAF.encode(), binary=True)   # not gzip: tolerated, like Poser
    assert _classify(lib, "pose/Pack/Fake.p2z")["kind"] == "injection"


def test_a_removal_package_that_also_holds_injection_leaves_is_a_mixed_injection(lib):
    lib.write("!DAZ/Fig/Deltas/InjDeltas.A.pz2", INJ_LEAF)
    lib.write("!DAZ/Fig/Deltas/RemDeltas.A.pz2", REM_LEAF)
    lib.write("pose/Pack/Both.pz2", _orchestrator(
        ":Runtime:libraries:!DAZ:Fig:Deltas:InjDeltas.A.pz2", ":Runtime:libraries:!DAZ:Fig:Deltas:RemDeltas.A.pz2"))
    rec = _classify(lib, "pose/Pack/Both.pz2")
    assert (rec["kind"], rec["mixed"]) == ("injection", True)


def test_stamps_record_every_file_read_and_notice_a_change(lib):
    leaf = lib.write("!DAZ/Fig/Deltas/InjDeltas.A.pz2", INJ_LEAF)
    entry = lib.write("pose/Pack/All.pz2", _orchestrator(":Runtime:libraries:!DAZ:Fig:Deltas:InjDeltas.A.pz2"))
    rec = li.classify_entry(entry, [lib.path])
    assert sorted(p for p, _m, _s in rec["stamps"]) == sorted([entry, leaf])
    assert li._stamps_current(rec["stamps"])
    with open(leaf, "a") as fh:
        fh.write("// edited\n")
    assert not li._stamps_current(rec["stamps"])


# ---- thumbnails, names, catalogs -----------------------------------------------------------

def test_find_thumbnail(tmp_path):
    folder = tmp_path
    for name in ("A.pz2", "A.PNG", "B.pz2", "B.rsr", "C.pz2", "D.pz2", "D.jpg", "D.rsr"):
        (folder / name).write_text("x")
    assert li.find_thumbnail(str(folder / "A.pz2")) == (str(folder / "A.PNG"), "image")
    assert li.find_thumbnail(str(folder / "B.pz2")) == ("", "rsr")
    assert li.find_thumbnail(str(folder / "C.pz2")) == ("", "none")
    assert li.find_thumbnail(str(folder / "D.pz2")) == (str(folder / "D.jpg"), "image")   # an image beats .rsr


def test_compose_name_respects_the_blender_id_limit():
    assert li.compose_name("BrowHeavy") == "BrowHeavy"
    assert li.compose_name("BrowHeavy", "A3-H3 MORExp1") == "BrowHeavy (A3-H3 MORExp1)"
    long = li.compose_name("BrowHeavy", "A very long product folder name that cannot fit anywhere")
    assert len(long.encode()) <= li.ID_NAME_LIMIT and long.startswith("BrowHeavy (") and long.endswith("…)")
    assert len(li.compose_name("é" * 60).encode()) <= li.ID_NAME_LIMIT
    assert len(li.compose_name("x" * 80, "folder").encode()) <= li.ID_NAME_LIMIT


def test_names_repeat_only_within_a_kind_and_stay_unique_per_folder():
    def e(path, kind):
        return {"path": path, "kind": kind, "stem": os.path.splitext(os.path.basename(path))[0],
                "folder": os.path.dirname(path)}
    entries = [e("/r/INJ A/BrowHeavy.pz2", "injection"), e("/r/INJ B/BrowHeavy.pz2", "injection"),
               e("/r/REM A/BrowHeavy.pz2", "removal"), e("/r/INJ A/Solo.pz2", "injection")]
    li.assign_display_names(entries)
    assert [x["name"] for x in entries] == ["BrowHeavy (INJ A)", "BrowHeavy (INJ B)", "BrowHeavy", "Solo"]
    # two long stems that trim to the same text in one folder must still get distinct names
    stems = ["S" * 70 + "1", "S" * 70 + "2"]
    clash = [e(f"/r/F/{s}.pz2", "injection") for s in stems]
    li.assign_display_names(clash)
    assert len({c["name"] for c in clash}) == 2


def test_catalog_path_and_blend_name():
    assert li.catalog_path("pose/!A3-H3 MORExp1 INJ") == "Pose/!A3-H3 MORExp1 INJ"
    assert li.catalog_path("pose/Weird: Name//x") == "Pose/Weird- Name/x"
    # Only the category folder is capitalized; the Runtime name goes on top.
    assert li.catalog_path("Pose/morphs/x", "Base Figures") == "Base Figures/Pose/morphs/x"
    assert li.catalog_path("pose/x", "A: B/C") == "A- B-C/Pose/x"
    a, b = li.blend_name_for("/r/Pack"), li.blend_name_for("/other/Pack")
    assert a != b and a.endswith(".blend") and a == li.blend_name_for("/r/Pack")
    assert " " not in li.blend_name_for("/r/My Folder!") and li.blend_name_for("/r/!!!").startswith("folder-")


# ---- build_manifest ------------------------------------------------------------------------

def _entries(manifest):
    return {os.path.relpath(e["path"], manifest["signature"]["roots"][0]).replace("\\", "/").split("libraries/")[1]: e
            for f in manifest["folders"] for e in f["entries"]}


def test_manifest_shows_injection_and_removal_only_and_counts_the_rest(library):
    m = li.build_manifest([library.path])
    assert m["stats"]["kinds"] == {"injection": 3, "removal": 1, "pose": 2, "other": 1}
    assert m["stats"]["entries"] == 7 and m["stats"]["shown"] == 4      # the 3 delta leaves are not entries
    assert sorted(_entries(m)) == ["pose/Fig INJ/Brow.pz2", "pose/Fig REM/Brow.pz2",
                                   "pose/Fig2 INJ/Brow.pz2", "pose/Fig2 INJ/Half.pz2"]
    assert m["stats"]["folders"] == 3 and len(m["folders"]) == 3


def test_manifest_entry_fields(library):
    entries = _entries(li.build_manifest([library.path]))
    inj, rem, half = entries["pose/Fig INJ/Brow.pz2"], entries["pose/Fig REM/Brow.pz2"], entries["pose/Fig2 INJ/Half.pz2"]
    assert inj["thumbnail"].endswith("Brow.png") and inj["thumbnail_kind"] == "image"
    assert (rem["thumbnail"], rem["thumbnail_kind"]) == ("", "rsr")
    assert inj["tags"] == ["injection", "poser"] and rem["tags"] == ["removal", "poser"]
    assert half["tags"] == ["injection", "incomplete", "poser"]
    assert half["description"] == "[INCOMPLETE: 1 of 2 files missing] Runtime/libraries/pose/Fig2 INJ/Half.pz2"
    assert inj["description"] == "Runtime/libraries/pose/Fig INJ/Brow.pz2"
    assert inj["catalog"] == "Content/Pose/Fig INJ" and inj["rel_path"] == "Runtime/libraries/pose/Fig INJ/Brow.pz2"
    # `Brow` repeats within the injection kind, so those two get their folder; the removal one is alone
    assert inj["name"] == "Brow (Fig INJ)" and entries["pose/Fig2 INJ/Brow.pz2"]["name"] == "Brow (Fig2 INJ)"
    assert rem["name"] == "Brow" and half["name"] == "Half"


def test_a_second_build_reuses_everything_and_a_touched_leaf_reclassifies_only_its_entries(library):
    first = li.build_manifest([library.path])
    second = li.build_manifest([library.path], previous=first)
    assert (second["stats"]["reused"], second["stats"]["classified"]) == (7, 0)
    assert second["folders"] == first["folders"]

    with open(os.path.join(library.path, "Runtime", "libraries", "!DAZ", "Fig", "Deltas", "RemDeltas.Brow.pz2"), "a") as fh:
        fh.write("// edited\n")
    third = li.build_manifest([library.path], previous=second)
    assert third["stats"]["classified"] == 1          # only the removal entry read that leaf


def test_changed_roots_discard_the_previous_answers(library, tmp_path):
    other = tmp_path / "Other"
    (other / "Runtime" / "libraries").mkdir(parents=True)
    first = li.build_manifest([library.path])
    second = li.build_manifest([library.path, str(other)], previous=first)
    assert second["stats"]["reused"] == 0
    assert li.build_manifest([library.path], previous=first, signature_extra="5.3")["stats"]["reused"] == 0


def test_changed_folders_lists_only_what_needs_writing_or_removing(library):
    first = li.build_manifest([library.path])
    assert len(li.changed_folders(None, first)["write"]) == 3
    assert li.changed_folders(first, li.build_manifest([library.path], previous=first)) == {"write": [], "remove": []}

    library.write("pose/Fig INJ/Brow.png", "a different thumbnail")
    os.utime(os.path.join(library.path, "Runtime", "libraries", "pose", "Fig INJ", "Brow.png"), (1, 1))
    second = li.build_manifest([library.path], previous=first)
    change = li.changed_folders(first, second)
    assert [f["dir"].endswith("Fig INJ") for f in change["write"]] == [True] and change["remove"] == []

    os.remove(os.path.join(library.path, "Runtime", "libraries", "pose", "Fig REM", "Brow.pz2"))
    third = li.build_manifest([library.path], previous=second)
    assert li.changed_folders(second, third)["remove"] == [li.blend_name_for(
        os.path.join(library.path, "Runtime", "libraries", "pose", "Fig REM"))]


def test_cancel_and_progress(library):
    calls = []
    li.build_manifest([library.path], progress=lambda phase, done, total: calls.append((phase, done, total)))
    assert calls[-1] == ("classifying", 7, 7)
    assert ("reading", 10, 10) in calls          # the 3 delta leaves are read too: 7 entries + 3
    with pytest.raises(li.BuildCancelled):
        li.build_manifest([library.path], cancelled=lambda: True)
    with pytest.raises(li.BuildCancelled):
        li.prefetch_heads([__file__] * 3, cancelled=lambda: True)


def test_prefetching_changes_nothing_but_speed(library):
    assert li.build_manifest([library.path], prefetch=False) == li.build_manifest([library.path], prefetch=True)
    li.prefetch_heads(["/definitely/not/there.pz2", __file__])           # failures are ignored, not raised


def test_an_incremental_build_does_not_prefetch(library, monkeypatch):
    first = li.build_manifest([library.path])
    monkeypatch.setattr(li, "prefetch_heads", lambda *a, **k: pytest.fail("prefetched an incremental build"))
    li.build_manifest([library.path], previous=first)


def test_manifest_round_trips_through_disk(library, tmp_path):
    first = li.build_manifest([library.path])
    path = str(tmp_path / "manifest.json")
    li.save_manifest(path, first)
    loaded = li.load_manifest(path)
    assert loaded["folders"] == first["folders"] and loaded["signature"] == first["signature"]
    assert li.build_manifest([library.path], previous=loaded)["stats"]["reused"] == 7
    assert li.load_manifest(str(tmp_path / "missing.json")) is None
    for name, content in (("notjson", b"{not json"), ("oldformat", b'{"version": 0}'), ("nogzip", b"plain text")):
        target = tmp_path / name
        target.write_bytes(content if name == "nogzip" else gzip.compress(content))
        assert li.load_manifest(str(target)) is None, name
    truncated = tmp_path / "truncated"
    truncated.write_bytes((tmp_path / "manifest.json").read_bytes()[:50])
    assert li.load_manifest(str(truncated)) is None


def test_nothing_is_written_under_the_content_roots(library, tmp_path):
    def snapshot():
        return sorted((dp, f, os.stat(os.path.join(dp, f)).st_mtime_ns)
                      for dp, _d, fs in os.walk(library.path) for f in fs)
    before = snapshot()
    first = li.build_manifest([library.path])
    li.build_manifest([library.path], previous=first)
    assert snapshot() == before


# ---- against the real library (skipped when the drive is not mounted) ----------------------

real = pytest.mark.skipif(not os.path.isdir(_REAL_BASE), reason="Poser content drive not mounted")


@real
@pytest.mark.parametrize("rel, kind", [
    ("pose/!A3-H3 MORExp1 INJ/! All Morphs.pz2", "injection"),
    ("pose/!A3-H3 MORExp1 REM/BrowHeavy.pz2", "removal"),
    ("pose/!SP All Morphs INJ/!All Head.pz2", "injection"),
])
def test_real_entries(rel, kind):
    path = os.path.join(_REAL_BASE, "Runtime", "libraries", rel)
    assert li.classify_entry(path, [_REAL_BASE])["kind"] == kind


@real
def test_real_hide_files_are_not_removals():
    hide = [os.path.join(dp, f) for dp, _d, fs in os.walk(os.path.join(_REAL_BASE, "Runtime", "libraries", "!DAZ", "Victoria 3", "Body", "ChanVis"))
            for f in fs if f.startswith("Hide.")]
    assert hide, "expected the Victoria 3 ChanVis files"
    assert all(li.sniff_leaf(li.read_head(p)) != li.LEAF_REMOVAL for p in hide[:40])


_REAL_PROPS = _REAL_BASE.rsplit("/", 1)[0] + "/Scenes, Props and Objects"


@real
def test_real_visibility_files_are_not_removals_in_either_flavour():
    path = os.path.join(_REAL_BASE, "Runtime", "libraries", "!DNA", "LaRoo", "ChanVis", "H_Blink L_LR2_Hide.p2z")
    assert os.path.isfile(path), "expected the La Roo ChanVis files"
    assert li.classify_entry(path, [_REAL_BASE])["kind"] == "pose"


@pytest.mark.skipif(not os.path.isdir(_REAL_PROPS), reason="Poser content drive not mounted")
def test_real_vendor_removal_files_that_say_hidden_stay_removals():
    folder = os.path.join(_REAL_PROPS, "Runtime", "libraries", "!Adz", "HiroGen")
    entry = os.path.join(folder, "GlansDownturn2.RemDeltas.pz2")
    assert os.path.isfile(entry)
    assert li.classify_entry(entry, [_REAL_PROPS])["kind"] == "removal"
    entry_file = os.path.join(_REAL_PROPS, "Runtime", "libraries", "Pose", "AdzHiroGen Mors REM", "GlansDownturn2 REM.pz2")
    assert os.path.isfile(entry_file)
    assert li.classify_entry(entry_file, [_REAL_PROPS])["kind"] == "removal"      # reads that RemDeltas file


@real
def test_real_walk_finds_the_same_leaves_as_apply_does():
    for rel in ("pose/!V3 All Morphs INJ/! All Head Morphs.pz2", "pose/!A3-H3 MORExp1 INJ/! All Morphs.pz2"):
        path = os.path.join(_REAL_BASE, "Runtime", "libraries", rel)
        state = li._Visit([_REAL_BASE], {}, {})
        li._visit(path, 0, set(), state)
        leaves = resolve_package_files(path, extra_roots=[_REAL_BASE])["leaf_paths"]
        assert sum(state.kinds.values()) == len(leaves) and state.missing == 0


@real
def test_real_sniff_agrees_with_a_full_parse_where_it_matters():
    files = [os.path.join(dp, f) for dp, _d, fs in os.walk(os.path.join(_REAL_BASE, "Runtime", "libraries", "pose"))
             for f in fs if f.lower().endswith(".pz2")]
    random.Random(3).shuffle(files)
    checked = 0
    for path in files:
        head = li.read_head(path)
        if head is None or "readScript" in head:
            continue
        text = read_poser_file(path)
        channels = scan_channels(text)
        has_deltas = any(c.delta_count > 0 for c in channels)
        is_removal = any(c.display_name == "-" and c.delta_count == 0 and c.declares_deltas for c in channels)
        sniffed = li.sniff_leaf(head)
        if "removePMDFileMorphs" in text or "injectPMDFileMorphs" in text:
            assert sniffed == li.LEAF_PMD, path          # Poser commands for the binary-morph system
        elif has_deltas and not any(c.uses_binary_morph for c in channels):
            assert sniffed == li.LEAF_INJECTION, path
        elif is_removal and not has_deltas:
            assert sniffed == li.LEAF_REMOVAL, path
        checked += 1
        if checked >= 600:
            break
    assert checked >= 300


# ---- catalogs and the asset signature (used by the 7.3b build) -----------------------------

def test_catalog_ids_are_stable_and_include_parents(library):
    manifest = li.build_manifest([library.path])
    ids = li.catalog_ids(manifest)
    assert set(ids) == {"Content", "Content/Pose", "Content/Pose/Fig INJ", "Content/Pose/Fig REM",
                        "Content/Pose/Fig2 INJ"}
    assert ids == li.catalog_ids(li.build_manifest([library.path]))
    assert li.catalog_uuid("Content/Pose/Fig INJ") == ids["Content/Pose/Fig INJ"] != li.catalog_uuid("Content/Pose/Fig REM")


def test_catalog_file_text_has_the_version_line_and_one_line_per_catalog():
    text = li.catalog_file_text({"pose": "u1", "pose/Fig INJ": "u2"})
    lines = [l for l in text.splitlines() if l and not l.startswith("#")]
    assert lines == ["VERSION 1", "u1:pose:pose", "u2:pose/Fig INJ:Fig INJ"]


def test_the_asset_signature_changes_with_the_format_and_the_blender_version(library):
    assert li.asset_signature("5.3.0") != li.asset_signature("5.0.1")
    first = li.build_manifest([library.path], signature_extra=li.asset_signature("5.3.0"))
    other = li.build_manifest([library.path], previous=first, signature_extra=li.asset_signature("5.0.1"))
    assert other["stats"]["reused"] == 0


def test_only_poser_library_category_folders_hold_entries():
    assert li.in_poser_library("pose/!A3-H3 MORExp1 INJ")
    assert li.in_poser_library("Pose/AM/Wolf 2") and li.in_poser_library("Face")
    assert not li.in_poser_library("!Adz/HiroGen")
    assert not li.in_poser_library("morphs") and not li.in_poser_library("")


def test_vendor_folders_beside_the_categories_are_not_entries(lib):
    # The !Adz layout: leaf files named <morph>.InjDeltas, which the name rule doesn't catch.
    lib.write("!Adz/Hiro/Frenulum.InjDeltas.pz2", INJ_LEAF)
    lib.write("!Adz/Hiro/Frenulum.RemDeltas.pz2", REM_LEAF)
    lib.write("morphs/Loose.pz2", INJ_LEAF)
    lib.write("Pose/Hiro/Frenulum.pz2", _orchestrator(":Runtime:libraries:!Adz:Hiro:Frenulum.InjDeltas.pz2"))
    lib.write("Pose/Hiro/Frenulum Off.pz2", _orchestrator(":Runtime:libraries:!Adz:Hiro:Frenulum.RemDeltas.pz2"))
    m = li.build_manifest([lib.path])
    assert sorted(_entries(m)) == ["Pose/Hiro/Frenulum Off.pz2", "Pose/Hiro/Frenulum.pz2"]
    assert m["stats"]["entries"] == 2


# ---- catalogs grouped by Runtime, as in Poser's Library palette --------------------------

def test_runtime_names_use_the_label_else_the_folder_and_never_collide():
    roots = ["/a/Base Figures", "/b/Base Figures/", "/c/base figures", "/d/Hair"]
    names = li.runtime_names(roots, {"/d/Hair": "  My Hair  "})
    assert names["/a/Base Figures"] == "Base Figures"
    assert names["/b/Base Figures/"] == "Base Figures (b)"
    assert names["/c/base figures"] == "base figures (c)"            # case-insensitive clash
    assert names["/d/Hair"] == "My Hair"
    assert len({n.casefold() for n in names.values()}) == len(roots)


def test_each_runtime_is_its_own_top_catalog_and_pose_spellings_merge_within_it(tmp_path):
    def runtime(name, category):
        root = tmp_path / name
        leaf = root / "Runtime" / "libraries" / "!V" / "Deltas" / "InjDeltas.Brow.pz2"
        leaf.parent.mkdir(parents=True); leaf.write_text(INJ_LEAF)
        pkg = root / "Runtime" / "libraries" / category / "Pack" / "Brow.pz2"
        pkg.parent.mkdir(parents=True)
        pkg.write_text(_orchestrator(":Runtime:libraries:!V:Deltas:InjDeltas.Brow.pz2"))
        return str(root)
    lower, upper = runtime("Base Figures", "pose"), runtime("Extra", "Pose")
    m = li.build_manifest([lower, upper], runtime_labels={upper: "Poser 5"})
    assert m["layout"] == {lower: "Base Figures", upper: "Poser 5"}
    assert sorted(e["catalog"] for f in m["folders"] for e in f["entries"]) == [
        "Base Figures/Pose/Pack", "Poser 5/Pose/Pack"]


def test_renaming_a_runtime_reuses_every_answer_and_rewrites_only_its_folders(library):
    first = li.build_manifest([library.path])
    renamed = li.build_manifest([library.path], previous=first, runtime_labels={library.path: "Mine"})
    assert renamed["signature"] == first["signature"]
    assert renamed["stats"]["classified"] == 0                    # nothing re-read
    assert {e["catalog"].split("/")[0] for f in renamed["folders"] for e in f["entries"]} == {"Mine"}
    assert len(li.changed_folders(first, renamed)["write"]) == len(renamed["folders"])
    again = li.build_manifest([library.path], previous=renamed, runtime_labels={library.path: "Mine"})
    assert li.changed_folders(renamed, again)["write"] == []


# ---- packages that move bones are poses, whatever else they do (owner, 2026-11-17) -------

def _inline(*parts):
    """One file: readScript lines, then actor blocks, the way a pose file is laid out."""
    return "{\n" + "".join(parts) + "}\n"


def test_a_pose_that_injects_its_expression_morphs_is_an_armature_pose(lib):
    # V3 Spellcaster: readScript the InjDeltas it dials, then pose the body in the same file.
    lib.write("!DAZ/V3/Deltas/InjDeltas.Mouth O.pz2", INJ_LEAF)
    body = BONE_LEAF[BONE_LEAF.index("\tactor"):BONE_LEAF.rindex("}")]
    spell = lib.write("pose/V3 Spellcaster/LB_Spell 01.pz2", _inline(
        '\treadScript ":Runtime:libraries:!DAZ:V3:Deltas:InjDeltas.Mouth O.pz2"\n', body))
    assert li.classify_entry(spell, [lib.path])["kind"] == "armature"


def test_a_fit_package_that_also_moves_the_hip_is_hidden_and_a_plain_one_is_not(lib):
    lib.write("Channing/Deltas/InjDeltas.V3 Fit.pz2", INJ_LEAF)
    lib.write("Channing/Deltas/RemDeltas.V3 Fit.pz2", REM_LEAF)
    lib.write("Channing/Pose/Hip.pz2", BONE_LEAF)
    target = '\treadScript ":Runtime:libraries:Channing:Deltas:{}"\n'
    body = BONE_LEAF[BONE_LEAF.index("\tactor"):BONE_LEAF.rindex("}")]
    lib.write("pose/Fit INJ/V3 Fit INJ.pz2", _inline(target.format("InjDeltas.V3 Fit.pz2"), body))
    lib.write("pose/Fit REM/V3 Fit REM.pz2", _inline(target.format("RemDeltas.V3 Fit.pz2"), body))
    # Bone channels in a file the package loads count the same as in the package itself.
    lib.write("pose/Fit INJ/V3 Fit Nested.pz2", _orchestrator(
        ":Runtime:libraries:Channing:Deltas:InjDeltas.V3 Fit.pz2", ":Runtime:libraries:Channing:Pose:Hip.pz2"))
    lib.write("pose/Fit INJ/V3 Morph Only.pz2", _orchestrator(":Runtime:libraries:Channing:Deltas:InjDeltas.V3 Fit.pz2"))
    m = li.build_manifest([lib.path])
    assert sorted(_entries(m)) == ["pose/Fit INJ/V3 Morph Only.pz2"]
    assert m["stats"]["kinds"]["armature"] == 3


def test_thigh_length_marks_a_pose_file_without_any_bone_channel(lib):
    # A saved pose can end in a block holding only `thighLength` and an empty `figure`.
    lib.write("!DAZ/V3/Deltas/InjDeltas.Brow.pz2", INJ_LEAF)
    entry = lib.write("pose/Lady/Pose 01.pz2", _inline(
        '\treadScript ":Runtime:libraries:!DAZ:V3:Deltas:InjDeltas.Brow.pz2"\n',
        "thighLength 0.17748\n", "figure\n\t{\n\t}\n"))
    assert li.classify_entry(entry, [lib.path])["kind"] == "armature"
