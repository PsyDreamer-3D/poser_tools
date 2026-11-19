# SPDX-License-Identifier: GPL-3.0-or-later
"""Tests for core/cr2/texture_paths.py (Phase 8.3): finding a MAT's textures the way Poser does."""

import os

import pytest

from core.cr2.texture_paths import TextureIndex, is_no_map, resolve_texture

_CONTENT = "/run/media/jess-green/236a91c9-2598-40dd-b968-9c0f0be8a903/Artwork/Poser Content"


@pytest.fixture
def content(tmp_path):
    """Two content roots; the MAT lives in the first, some textures only in the second."""
    def touch(*parts):
        path = tmp_path.joinpath(*parts)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"img")
        return str(path)
    own, other = tmp_path / "Characters", tmp_path / "Clothing"
    mat = touch("Characters", "Runtime", "libraries", "pose", "Lady", "MAT.pz2")
    files = {
        "body": touch("Characters", "Runtime", "textures", "DAZ", "Lady", "Body.jpg"),
        "eyes": touch("Clothing", "Runtime", "textures", "Lady", "Eyes.jpg"),   # referenced as Textures
        "leather_p3d": touch("Clothing", "Runtime", "textures", "p3d", "tanleather.jpg"),
        "leather_wiz": touch("Clothing", "Runtime", "textures", "the3dwizard", "tanleather.jpg"),
        "lace_own": touch("Characters", "Runtime", "textures", "lace.jpg"),
        "lace_other": touch("Clothing", "Runtime", "textures", "misc", "lace.jpg"),
    }
    roots = [str(own), str(other)]
    return mat, roots, TextureIndex(roots), files


def test_no_map_in_any_spelling_is_no_texture():
    assert is_no_map("NO_MAP") and is_no_map('":NO_MAP:"') and is_no_map(":no_map:")
    assert resolve_texture(":NO_MAP:", "/x/MAT.pz2") == (None, "no map")


def test_a_full_colon_path_resolves_as_written(content):
    mat, roots, index, files = content
    assert resolve_texture(":Runtime:Textures:DAZ:Lady:Body.jpg", mat, roots, index) == (files["body"], "")


def test_paths_relative_to_runtime_and_to_runtime_textures(content):
    mat, roots, index, files = content
    assert resolve_texture(":textures:DAZ:Lady:Body.jpg", mat, roots, index) == (files["body"], "relative to Runtime")
    assert resolve_texture(":DAZ:Lady:Body.jpg", mat, roots, index) == (files["body"], "relative to Runtime:Textures")
    # In another configured root, and missing its leading colon.
    assert resolve_texture("Runtime:Textures:Lady:Eyes.jpg", mat, roots, index) == (
        files["eyes"], "colon path without its leading ':'")


def test_a_windows_path_is_found_by_its_runtime_tail_in_the_same_folder(content):
    mat, roots, index, files = content
    ref = "D:\\Program Files\\Metacreations\\Poser 4\\Runtime\\Textures\\p3d\\tanleather.jpg"
    assert resolve_texture(ref, mat, roots, index) == (
        files["leather_p3d"], "absolute Windows path, found under its Runtime folder")


def test_by_file_name_prefers_shared_folders_then_the_mats_own_root(content):
    mat, roots, index, files = content
    # No Runtime in the path: the name decides, and the folder before it breaks the tie.
    path, note = resolve_texture("D:\\My Documents\\the3dwizard\\tanleather.jpg", mat, roots, index)
    assert path == files["leather_wiz"] and note == "found by file name, chosen from 2 files of that name"
    path, note = resolve_texture("lace.jpg", mat, roots, index)
    assert path == files["lace_own"] and "chosen from 2" in note
    assert resolve_texture("Eyes.jpg", mat, roots, index) == (files["eyes"], "found by file name")


def test_missing_stays_missing_and_no_index_means_no_name_search(content):
    mat, roots, index, files = content
    assert resolve_texture(":DAZ:Nope.jpg", mat, roots, index) == (None, "not found")
    assert resolve_texture("lace.jpg", mat, roots, None) == (None, "not found")


@pytest.mark.skipif(not os.path.isdir(_CONTENT), reason="Poser content drive not mounted")
def test_real_references_from_the_owners_mat_poses():
    roots = [os.path.join(_CONTENT, r) for r in ("Base Figures", "Clothing", "Custom Character Sets", "PsyDreamer3D/PsyDreamer3D")]
    index = TextureIndex(roots)
    boots = os.path.join(_CONTENT, "Clothing/Runtime/libraries/pose/TreadZ MAT/WorkBootZ.pz2")
    path, note = resolve_texture("D:\\Program Files\\Metacreations\\Poser 4\\Runtime\\Textures\\p3d\\tanleather.jpg",
                                 boots, roots, index)
    assert path.endswith(os.path.join("Clothing", "Runtime", "textures", "p3d", "tanleather.jpg"))
    cookie = os.path.join(_CONTENT, "Custom Character Sets/Runtime/libraries/Pose/CookieMilCat/MATCookie.PZ2")
    path, _note = resolve_texture("C:\\Program Files\\Curious Labs\\Poser4\\Runtime\\textures\\CookieMilCat\\CookieMilCat.tif",
                                  cookie, roots, index)
    assert path and path.endswith("CookieMilCat.tif")


def test_a_poser_bum_uses_the_image_it_was_made_from(content):
    mat, roots, index, files = content
    path, note = resolve_texture(":Runtime:Textures:DAZ:Lady:Body.bum", mat, roots, index)
    assert path == files["body"] and note.startswith("Poser .bum bump map, used the image it was made from")
    assert resolve_texture(":DAZ:Lady:Nothing.bum", mat, roots, index) == (None, "not found")


def test_a_bum_whose_source_image_is_gone_is_reported_as_such(content):
    mat, roots, index, files = content
    bum = os.path.join(roots[0], "Runtime", "textures", "Orphan.bum")
    with open(bum, "wb") as fh:
        fh.write(b"BM")
    path, note = resolve_texture(":Runtime:textures:Orphan.bum", mat, roots, index)
    assert path is None and note.startswith("Poser .bum without the image it was made from")
