#!/usr/bin/env python3
"""Generate a Blender extension repository index.json from blender_manifest.toml.

Run after scripts/build_dev.sh (or the release workflow) has produced a zip
in dist/. This is only needed if you're hosting a self-managed extensions
repository (index.json + zips) for Blender's "Custom Repository" feature —
plain GitHub Releases don't require it, and the standard release.yml in
this scaffold does not call it.
"""

import glob
import hashlib
import json
import os
import sys

try:
    import tomllib
except ImportError:
    try:
        import tomli as tomllib
    except ImportError:
        print("Error: tomllib (Python 3.11+) or tomli package is required.")
        sys.exit(1)

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DIST_DIR = os.path.join(REPO_ROOT, "dist")
INDEX_PATH = os.path.join(DIST_DIR, "index.json")


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


MANIFEST_PATH = os.path.join(find_package_dir(), "blender_manifest.toml")


def sha256_of_file(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def main() -> None:
    with open(MANIFEST_PATH, "rb") as f:
        manifest = tomllib.load(f)

    ext_id = manifest["id"]
    version = manifest["version"]
    zip_name = f"{ext_id}-{version}.zip"
    zip_path = os.path.join(DIST_DIR, zip_name)

    if not os.path.isfile(zip_path):
        matches = sorted(
            glob.glob(os.path.join(DIST_DIR, f"{ext_id}-*.zip")),
            key=os.path.getmtime,
            reverse=True,
        )
        if not matches:
            print(f"Error: no zip found in {DIST_DIR} for extension '{ext_id}'.")
            sys.exit(1)
        zip_path = matches[0]
        zip_name = os.path.basename(zip_path)

    archive_hash = "sha256:" + sha256_of_file(zip_path)
    archive_url = f"./{zip_name}"

    entry = {
        "id": ext_id,
        "name": manifest["name"],
        "tagline": manifest["tagline"],
        "version": version,
        "type": manifest["type"],
        "archive_url": archive_url,
        "archive_hash": archive_hash,
        "blender_version_min": manifest["blender_version_min"],
        "license": manifest["license"],
        # .get(), not [...]: "tags" is optional in the manifest schema and
        # has been omitted in some projects in this workspace.
        "tags": manifest.get("tags", []),
        "maintainer": manifest["maintainer"],
    }
    if "category" in manifest:
        entry["category"] = manifest["category"]

    index: dict = {"version": "v1", "blocklist": [], "data": []}
    if os.path.isfile(INDEX_PATH):
        with open(INDEX_PATH, "r", encoding="utf-8") as f:
            index = json.load(f)

    data: list = index.setdefault("data", [])
    for i, existing in enumerate(data):
        if existing.get("id") == ext_id:
            data[i] = entry
            break
    else:
        data.append(entry)

    os.makedirs(DIST_DIR, exist_ok=True)
    with open(INDEX_PATH, "w", encoding="utf-8") as f:
        json.dump(index, f, indent=2)
        f.write("\n")

    print(f"Wrote {INDEX_PATH}")


if __name__ == "__main__":
    main()
