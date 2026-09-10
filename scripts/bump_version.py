#!/usr/bin/env python3
"""Bump the add-on version in blender_manifest.toml (and a legacy bl_info
tuple in __init__.py, if one is still present).

Usage:
    python3 scripts/bump_version.py <version>

<version> must be a semantic-version string, e.g. "1.2.3".
A leading "v" is accepted and stripped automatically.

Files updated
-------------
<package>/blender_manifest.toml   version = "<major>.<minor>.<patch>"
<package>/__init__.py             "version": (<major>, <minor>, <patch>)
                                   — only if a bl_info dict with a version
                                   tuple is found. Add-ons built purely on
                                   blender_manifest.toml (no bl_info) don't
                                   need this, so a miss here is informational,
                                   not an error.

<package> is auto-detected as the only first-level subdirectory of the repo
root that contains a blender_manifest.toml (one add-on per repo — see
../AGENTS.md section 2). This means this script needs zero edits when
copied into a new project.
"""

import os
import re
import sys

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def find_package_dir() -> str:
    """Return the path to the single subdirectory containing blender_manifest.toml."""
    candidates = []
    for entry in sorted(os.listdir(REPO_ROOT)):
        path = os.path.join(REPO_ROOT, entry)
        if os.path.isdir(path) and os.path.isfile(os.path.join(path, "blender_manifest.toml")):
            candidates.append(path)

    if not candidates:
        raise SystemExit("Error: no subdirectory containing blender_manifest.toml was found.")
    if len(candidates) > 1:
        names = ", ".join(os.path.basename(c) for c in candidates)
        raise SystemExit(
            f"Error: multiple blender_manifest.toml files found ({names}). "
            "This repo layout assumes one add-on per repo."
        )
    return candidates[0]


PACKAGE_DIR = find_package_dir()
MANIFEST_PATH = os.path.join(PACKAGE_DIR, "blender_manifest.toml")
INIT_PATH = os.path.join(PACKAGE_DIR, "__init__.py")


def parse_version(raw: str) -> tuple[int, int, int]:
    """Return (major, minor, patch) from a string like 'v1.2.3' or '1.2.3'."""
    raw = raw.lstrip("v").strip()
    parts = raw.split(".")
    if len(parts) != 3 or not all(p.isdigit() for p in parts):
        raise SystemExit(
            f"Error: version must be in MAJOR.MINOR.PATCH format, got {raw!r}"
        )
    return int(parts[0]), int(parts[1]), int(parts[2])


def update_manifest(version_str: str) -> None:
    """Replace the version field in blender_manifest.toml."""
    with open(MANIFEST_PATH, "r", encoding="utf-8") as f:
        content = f.read()

    updated, count = re.subn(
        r'^(version\s*=\s*")[^"]*(")',
        rf'\g<1>{version_str}\2',
        content,
        flags=re.MULTILINE,
    )
    if count == 0:
        raise SystemExit("Error: could not find 'version = \"...\"' in blender_manifest.toml")

    with open(MANIFEST_PATH, "w", encoding="utf-8") as f:
        f.write(updated)
    print(f"blender_manifest.toml -> version = \"{version_str}\"")


def update_init(major: int, minor: int, patch: int) -> None:
    """Replace a legacy bl_info version tuple in __init__.py, if present.

    Modern (manifest-only) add-ons have no bl_info dict at all — that's
    expected, not an error, so a miss here only prints a note.
    """
    with open(INIT_PATH, "r", encoding="utf-8") as f:
        content = f.read()

    updated, count = re.subn(
        r'("version"\s*:\s*)\(\s*\d+\s*,\s*\d+\s*,\s*\d+\s*\)',
        rf'\g<1>({major}, {minor}, {patch})',
        content,
    )
    if count == 0:
        print("__init__.py           -> no bl_info version tuple found (skipped; manifest-only add-on)")
        return

    with open(INIT_PATH, "w", encoding="utf-8") as f:
        f.write(updated)
    print(f"__init__.py           -> \"version\": ({major}, {minor}, {patch})")


def main() -> None:
    if len(sys.argv) != 2:
        raise SystemExit("Usage: bump_version.py <version>  (e.g. 1.2.3 or v1.2.3)")

    major, minor, patch = parse_version(sys.argv[1])
    version_str = f"{major}.{minor}.{patch}"

    update_manifest(version_str)
    update_init(major, minor, patch)


if __name__ == "__main__":
    main()
