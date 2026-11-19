# SPDX-License-Identifier: GPL-3.0-or-later
"""Tests for core/cr2/mat_pose.py (Phase 8): reading a MAT pose and matching it to FBX slots."""

import os

import pytest

from core.cr2.mat_pose import match_materials_to_slots, read_mat_pose, slot_prefix

AIKO3_SLOTS = ["Figure_SkinTorso", "Figure_SkinKnee", "Figure_SkinShin", "Figure_SkinThigh",
               "Figure_Lips", "Figure_LIris", "Figure_RIris"]
_CONTENT = "/run/media/jess-green/236a91c9-2598-40dd-b968-9c0f0be8a903/Artwork/Poser Content"
_ASSETS = "/home/jess-green/Documents/Blender_Add-on_Dev/Test_Poser_Assets"


def test_the_prefix_is_the_object_name_or_what_every_slot_shares():
    assert slot_prefix(AIKO3_SLOTS, "Figure") == "Figure_"
    assert slot_prefix(["LaFemme_Pro_SkinTorso", "LaFemme_Pro_Iris"], "Renamed") == "LaFemme_Pro_"
    assert slot_prefix(["SkinTorso", "Lips"]) == ""
    # Every slot also sharing "Skin" doesn't eat into the names: the cut is at the last `_`.
    assert slot_prefix(["Fig_SkinA", "Fig_SkinB"]) == "Fig_"
    assert slot_prefix(["Fig_", "Fig_A"]) == ""          # would leave an empty name


def test_a_v3_mat_on_aiko3_matches_by_name_and_reports_the_rest():
    result = match_materials_to_slots(["SkinTorso", "SkinLeg", "Lips", "LIris", "Preview"], AIKO3_SLOTS, "Figure")
    assert result["pairs"] == [("SkinTorso", "Figure_SkinTorso"), ("Lips", "Figure_Lips"), ("LIris", "Figure_LIris")]
    assert result["no_slot"] == ["SkinLeg"]                # Preview is never on a mesh: not reported
    assert result["untouched"] == ["Figure_SkinKnee", "Figure_SkinShin", "Figure_SkinThigh", "Figure_RIris"]


def test_duplicate_suffixes_and_case_are_tolerated_but_exact_names_win():
    slots = ["Figure_SkinTorso.001", "Figure_lips", "Figure_Lips"]
    result = match_materials_to_slots(["SkinTorso", "Lips", "LIPS"], slots, "Figure")
    assert result["pairs"] == [("SkinTorso", "Figure_SkinTorso.001"), ("Lips", "Figure_Lips"), ("LIPS", "Figure_lips")]


@pytest.mark.skipif(not os.path.isdir(_CONTENT), reason="Poser content drive not mounted")
def test_real_lady_xtaci_mat_against_aiko3s_own_material_names():
    mats = read_mat_pose(os.path.join(_CONTENT, "Custom Character Sets/Runtime/libraries/Pose/Lady Xtaci/! MAT Lady Xtaci !.pz2"))
    aiko3 = read_mat_pose(os.path.join(_ASSETS, "!Aiko 3.cr2"))
    slots = ["Figure_" + n for n in aiko3 if n != "Preview"]
    result = match_materials_to_slots(list(mats), slots, "Figure")
    assert len(mats) == 33 and len(result["pairs"]) == 31
    assert result["no_slot"] == ["SkinLeg"]
    assert result["untouched"] == ["Figure_SkinKnee", "Figure_SkinShin", "Figure_SkinThigh"]
    assert mats["SkinTorso"].diffuse_tex.endswith("LadyXtaci_Body.jpg")
