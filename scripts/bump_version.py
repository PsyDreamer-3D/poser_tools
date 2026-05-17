#!/usr/bin/env python3
"""Bump the add-on version in blender_manifest.toml and __init__.py.

Usage:
    python3 scripts/bump_version.py <version>

<version> must be a semantic-version string, e.g. "1.2.3".
A leading "v" is accepted and stripped automatically.

Files updated
-------------
blender_manifest.toml   version = "<major>.<minor>.<patch>"
__init__.py             "version": (<major>, <minor>, <patch>)
"""

import os
import re
import sys

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MANIFEST_PATH = os.path.join(REPO_ROOT, "blender_manifest.toml")
INIT_PATH = os.path.join(REPO_ROOT, "__init__.py")


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
    print(f"blender_manifest.toml → version = \"{version_str}\"")


def update_init(major: int, minor: int, patch: int) -> None:
    """Replace the version tuple in the bl_info dict inside __init__.py."""
    with open(INIT_PATH, "r", encoding="utf-8") as f:
        content = f.read()

    updated, count = re.subn(
        r'("version"\s*:\s*)\(\s*\d+\s*,\s*\d+\s*,\s*\d+\s*\)',
        rf'\g<1>({major}, {minor}, {patch})',
        content,
    )
    if count == 0:
        raise SystemExit('Error: could not find "version": (...) in __init__.py')

    with open(INIT_PATH, "w", encoding="utf-8") as f:
        f.write(updated)
    print(f"__init__.py          → \"version\": ({major}, {minor}, {patch})")


def main() -> None:
    if len(sys.argv) != 2:
        raise SystemExit("Usage: bump_version.py <version>  (e.g. 1.2.3 or v1.2.3)")

    major, minor, patch = parse_version(sys.argv[1])
    version_str = f"{major}.{minor}.{patch}"

    update_manifest(version_str)
    update_init(major, minor, patch)


if __name__ == "__main__":
    main()
