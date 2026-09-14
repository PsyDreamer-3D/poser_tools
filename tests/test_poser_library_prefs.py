# SPDX-License-Identifier: GPL-3.0-or-later
"""Tests for core/cr2/poser_library_prefs.py."""

import glob
import os

import pytest

from core.cr2.poser_library_prefs import find_library_prefs_files, parse_content_folders

# The owner's own live Poser install (via Wine) -- machine-specific, not
# always present. Real-file tests skip cleanly when it isn't there.
_REAL_LIBRARY_PREFS_GLOB = os.path.join(
    os.path.expanduser("~"), ".wine*", "drive_c", "users", "*",
    "AppData", "Roaming", "Poser", "*", "LibraryPrefs.xml",
)


def _write_prefs(path, *folders):
    entries = "\n".join(
        f'\t<ContentFolder folder="{f}" index="{i}" searchIndexed="1" />'
        for i, f in enumerate(folders)
    )
    path.write_text(
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<LibraryPreferences curFolder="0" version="1">\n'
        f"{entries}\n"
        "</LibraryPreferences>\n"
    )


def test_parse_content_folders_resolves_plain_absolute_path(tmp_path):
    root = tmp_path / "MyPoserLib"
    (root / "Runtime" / "libraries").mkdir(parents=True)
    prefs = tmp_path / "LibraryPrefs.xml"
    _write_prefs(prefs, str(root / "Runtime" / "libraries"))

    assert parse_content_folders(str(prefs)) == [str(root.resolve())]


def test_parse_content_folders_is_case_insensitive_on_runtime_libraries(tmp_path):
    root = tmp_path / "MyPoserLib"
    (root / "Runtime" / "Libraries").mkdir(parents=True)
    prefs = tmp_path / "LibraryPrefs.xml"
    _write_prefs(prefs, str(root / "Runtime" / "Libraries"))

    assert parse_content_folders(str(prefs)) == [str(root.resolve())]


def test_parse_content_folders_skips_nonexistent_paths(tmp_path):
    prefs = tmp_path / "LibraryPrefs.xml"
    _write_prefs(prefs, r"C:\Nowhere\Runtime\libraries")

    assert parse_content_folders(str(prefs)) == []


def test_parse_content_folders_dedupes_by_real_path(tmp_path):
    root = tmp_path / "MyPoserLib"
    (root / "Runtime" / "libraries").mkdir(parents=True)
    prefs = tmp_path / "LibraryPrefs.xml"
    _write_prefs(
        prefs,
        str(root / "Runtime" / "libraries"),
        str(root / "Runtime" / "libraries") + "/",
    )

    assert parse_content_folders(str(prefs)) == [str(root.resolve())]


def test_parse_content_folders_ignores_malformed_xml(tmp_path):
    prefs = tmp_path / "LibraryPrefs.xml"
    prefs.write_text("not xml at all <<<")

    assert parse_content_folders(str(prefs)) == []


def test_parse_content_folders_ignores_missing_file(tmp_path):
    assert parse_content_folders(str(tmp_path / "nope.xml")) == []


@pytest.mark.skipif(os.name == "nt", reason="Wine drive-letter mapping is POSIX-only")
def test_parse_content_folders_resolves_wine_z_drive(tmp_path):
    # Emulate a Wine prefix layout: xml lives under .../drive_c/..., and a
    # Z:\ path in the xml should map back to the real filesystem root.
    prefix = tmp_path / "wineprefix" / "drive_c" / "users" / "me" / "AppData" / "Roaming" / "Poser" / "13"
    prefix.mkdir(parents=True)
    prefs = prefix / "LibraryPrefs.xml"

    real_root = tmp_path / "ExternalDrive" / "Poser Content"
    (real_root / "Runtime" / "libraries").mkdir(parents=True)

    # Z:\<path with the leading / stripped> stands in for the real absolute path.
    windows_style = "Z:" + str(real_root / "Runtime" / "libraries")
    _write_prefs(prefs, windows_style)

    assert parse_content_folders(str(prefs)) == [str(real_root.resolve())]


@pytest.mark.skipif(os.name == "nt", reason="Wine drive-letter mapping is POSIX-only")
def test_parse_content_folders_resolves_wine_c_drive_from_own_prefix(tmp_path):
    prefix_root = tmp_path / "wineprefix"
    appdata = prefix_root / "drive_c" / "users" / "me" / "AppData" / "Roaming" / "Poser" / "13"
    appdata.mkdir(parents=True)
    prefs = appdata / "LibraryPrefs.xml"

    content_root = prefix_root / "drive_c" / "users" / "Public" / "Documents" / "Poser Content"
    (content_root / "Runtime" / "libraries").mkdir(parents=True)

    _write_prefs(prefs, r"C:\users\Public\Documents\Poser Content\Runtime\libraries")

    assert parse_content_folders(str(prefs)) == [str(content_root.resolve())]


def test_parse_content_folders_skips_relative_legacy_entries(tmp_path):
    # Older Poser schema sometimes stores bare relative paths -- unresolvable
    # without knowing Poser's own launch cwd, so these must be dropped, not
    # misinterpreted as relative to the xml file or cwd.
    prefs = tmp_path / "LibraryPrefs.xml"
    _write_prefs(prefs, "Runtime\\libraries", "Downloads\\Runtime\\libraries")

    assert parse_content_folders(str(prefs)) == []


def test_find_library_prefs_files_returns_empty_list_when_none_present(monkeypatch, tmp_path):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.delenv("APPDATA", raising=False)
    assert find_library_prefs_files() == []


def test_find_library_prefs_files_against_real_install():
    matches = glob.glob(_REAL_LIBRARY_PREFS_GLOB)
    if not matches:
        pytest.skip("No real Wine-hosted Poser LibraryPrefs.xml found on this machine")

    found = find_library_prefs_files()
    assert found  # at least the real file(s) above should turn up

    resolved = parse_content_folders(found[0])
    assert resolved, f"Expected at least one resolvable content folder in {found[0]}"
