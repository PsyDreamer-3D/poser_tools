# SPDX-License-Identifier: GPL-3.0-or-later
"""
poser_library_prefs.py
-----------------------
Reads Poser's own LibraryPrefs.xml -- the file Poser itself writes with every
content folder the user has registered -- so poser_tools' Runtime-root
preferences can be imported from it instead of the user retyping every path
by hand. No cr2_importer equivalent; this is original poser_tools code.

Schema confirmed stable from a 2005-era Poser 5 install through a live
Poser 13 one:

    <LibraryPreferences ...>
        <ContentFolder folder="Z:\\...\\Runtime\\libraries" index="N" .../>
        ...
    </LibraryPreferences>

Only the <ContentFolder folder="..."> attribute is read. Nested <Library>
elements are Poser's own "last opened subfolder" state and <CollectionFolder>
is an in-app saved-collection feature -- neither is a filesystem root.
"""

import glob
import os
import xml.etree.ElementTree as ET
from typing import List


def find_library_prefs_files() -> List[str]:
    """Locate the *live* LibraryPrefs.xml file(s) Poser itself reads/writes.

    Deliberately not a filesystem-wide search -- a stale copy can be sitting
    inside old archived content (observed in the wild, bundled inside a
    Runtime folder's own "prefs" subfolder), and treating that as current
    would silently feed in outdated or wrong roots. Only the standard
    per-platform preferences location is checked.
    """
    patterns = []
    if os.name == "nt":
        appdata = os.environ.get("APPDATA")
        if appdata:
            patterns.append(os.path.join(appdata, "Poser", "*", "LibraryPrefs.xml"))
    else:
        home = os.path.expanduser("~")
        # Wine: one or more prefixes (~/.wine, ~/.wine-poser13, ...), each its
        # own "C: drive" under drive_c, with Poser's real Windows-side
        # AppData path underneath.
        patterns.append(os.path.join(
            home, ".wine*", "drive_c", "users", "*", "AppData", "Roaming",
            "Poser", "*", "LibraryPrefs.xml",
        ))

    found = []
    for pattern in patterns:
        found.extend(glob.glob(pattern))
    return sorted(set(found))


def _strip_runtime_libraries_suffix(path: str) -> str:
    """Strip a trailing Runtime/libraries segment (case-insensitive) from an
    already forward-slashed path, returning the content root
    poser_paths.py/runtime_index.py expect (the folder containing Runtime).
    """
    parts = path.rstrip("/").split("/")
    if len(parts) >= 2 and parts[-1].lower() == "libraries" and parts[-2].lower() == "runtime":
        parts = parts[:-2]
    return "/".join(parts)


def _resolve_wine_drive_letter(normalized: str, xml_path: str) -> str:
    """Translate a Windows drive-lettered path (already forward-slashed) to
    its real location under the Wine prefix that owns xml_path.

    Z: is Wine's standard default mapping to the Unix filesystem root. Any
    other letter (typically C:) is resolved against that *same* prefix's own
    drive_c directory, derived from xml_path itself -- not a guessed default
    prefix location, since the file we just read already tells us which
    prefix it lives under. Returns the path unchanged if no translation is
    possible; the caller's os.path.isdir() check drops anything bogus.
    """
    drive = normalized[0].upper()
    rest = normalized[2:].lstrip("/")
    if drive == "Z":
        return "/" + rest
    idx = xml_path.replace("\\", "/").find("/drive_c/")
    if idx == -1:
        return normalized
    drive_c = xml_path[:idx + len("/drive_c")]
    return drive_c + "/" + rest


def parse_content_folders(xml_path: str) -> List[str]:
    """Return the resolved, existing, de-duplicated content-root paths named
    by every <ContentFolder folder="..."> in xml_path.

    Anything that doesn't resolve to a real directory (a moved drive, content
    since removed, a drive letter with no known Wine mapping, a relative path
    from an old schema) is silently skipped -- same best-effort convention
    poser_paths.resolve_poser_path() already uses elsewhere in this project.
    """
    try:
        tree = ET.parse(xml_path)
    except (ET.ParseError, OSError):
        return []

    seen = set()
    result = []
    for elem in tree.getroot().iter("ContentFolder"):
        raw = elem.get("folder")
        if not raw:
            continue
        normalized = raw.replace("\\", "/")
        if os.name != "nt" and len(normalized) >= 2 and normalized[1] == ":":
            normalized = _resolve_wine_drive_letter(normalized, xml_path)
        root = _strip_runtime_libraries_suffix(normalized)
        if not root or not os.path.isdir(root):
            continue
        real = os.path.realpath(root)
        if real not in seen:
            seen.add(real)
            result.append(real)
    return result
