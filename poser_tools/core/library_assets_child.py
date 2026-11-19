# SPDX-License-Identifier: GPL-3.0-or-later
"""
library_assets_child.py
-----------------------
Phase 7.3b: runs inside a *child* Blender process
(`blender --background --factory-startup --python library_assets_child.py -- job.json`),
never imported by the add-on. For each source folder in the job it creates one stand-in
`Scene` asset per package, gives it the Poser thumbnail (or a placeholder), tags,
catalog and payload, and writes the folder's `.blend`.

Why a separate process and not the user's session: `bpy.data.libraries.write` needs the
datablocks to exist, so building ~4,000 asset-marked stand-ins in the session the user is
working in would add and remove them from their file (they would pass through "Current
File" assets and the undo stack), and freeze the UI for ~10 s unless sliced.

Stand-in type is `Scene`, chosen against what each type does in Blender (docs/handoff-morph-
injection.md, "7.3 correction"). The Asset Browser lists only Action, Brush, Collection,
Material, Node Tree, Object, Scene and World, so an `Image` library (the first choice) shows
catalogs and "No items" there. `Action` is taken wholesale by the Pose Library shelf and
`NodeTree` feeds the Add menus; `Object`, `World` and `Material` are drag-and-drop targets (a
dropped stand-in would add an empty, replace the world, or assign a blank material); `Collection`
has no thumbnail on 5.0.1. A `Scene` has no drop target. `Mesh`/`Text` can't hold a thumbnail.

This file must stay stand-alone (stdlib, bpy, numpy only): the child has no add-on on its
path. The parent (core/library_build.py) plans the work, writes the catalog file and
deletes stale files; this only builds datablocks. It talks back on stdout:
  POSER_PROGRESS <done> <total>   after each folder
  POSER_DONE {json}               summary, last line on success
  POSER_ERROR <message>           before a non-zero exit
"""

import json
import os
import sys
import time
import traceback

import bpy
import numpy as np

# A preview larger than this only costs file size; the shelf draws previews small.
PREVIEW_MAX = 256
PLACEHOLDER_SIZE = 128
# Flat tiles by kind, with a glyph so they read apart even without colour: a plus for
# injection, a minus for removal, a filled swatch for a MAT pose. Blender can't draw text onto an image in a background
# process, and the shelf prints the name under the tile anyway.
_TILE_COLORS = {"injection": (0.17, 0.36, 0.58), "removal": (0.58, 0.38, 0.17),
                "material": (0.42, 0.24, 0.55)}
_DEFAULT_TILE = (0.35, 0.35, 0.35)


def placeholder_pixels(kind: str) -> np.ndarray:
    """A PLACEHOLDER_SIZE square RGBA float tile for `kind`."""
    n = PLACEHOLDER_SIZE
    color = np.array(_TILE_COLORS.get(kind, _DEFAULT_TILE), dtype=np.float32)
    tile = np.empty((n, n, 4), dtype=np.float32)
    tile[..., :3] = color
    tile[..., 3] = 1.0
    light = np.minimum(color + 0.18, 1.0)
    tile[:3, :, :3] = tile[-3:, :, :3] = tile[:, :3, :3] = tile[:, -3:, :3] = light
    bar, half = n // 10, n // 2
    glyph = np.array([0.95, 0.95, 0.95], dtype=np.float32)
    lo, hi = n // 4, n - n // 4
    if kind == "material":
        tile[lo:hi, lo:hi, :3] = glyph                                      # a swatch
        return tile
    tile[half - bar // 2:half + bar // 2 + 1, lo:hi, :3] = glyph          # horizontal bar: both kinds
    if kind != "removal":
        tile[lo:hi, half - bar // 2:half + bar // 2 + 1, :3] = glyph      # vertical bar too: a plus
    return tile


def load_thumbnail(path: str):
    """(width, height, flat float32 RGBA) for an image file, or None if Blender can't read it.

    Poser thumbnails are ~91 px; anything over PREVIEW_MAX is scaled down.
    """
    try:
        image = bpy.data.images.load(path)
    except RuntimeError:
        return None
    try:
        width, height = image.size
        if not width or not height:
            return None
        if max(width, height) > PREVIEW_MAX:
            factor = PREVIEW_MAX / max(width, height)
            image.scale(max(1, round(width * factor)), max(1, round(height * factor)))
            width, height = image.size
        pixels = np.empty(width * height * 4, dtype=np.float32)
        image.pixels.foreach_get(pixels)
        return width, height, pixels
    finally:
        bpy.data.images.remove(image)


def make_standin(entry: dict, catalog_ids: dict, counts: dict):
    """One asset-marked Scene for a manifest entry, with its preview, tags and payload."""
    scene = bpy.data.scenes.new(entry["name"])
    scene.asset_mark()
    data = scene.asset_data
    data.description = entry["description"]
    catalog_id = catalog_ids.get(entry["catalog"])
    if catalog_id:
        data.catalog_id = catalog_id
    for tag in entry["tags"]:
        data.tags.new(tag, skip_if_exists=True)
    # What the shelf click needs to find the package again. Identity is (root, rel_path),
    # never the name: names get the folder appended when they repeat.
    data["poser_root"] = entry["root"]
    data["poser_rel_path"] = entry["rel_path"]
    data["poser_kind"] = entry["kind"]

    loaded = load_thumbnail(entry["thumbnail"]) if entry["thumbnail"] else None
    if loaded is not None:
        width, height, pixels = loaded
        counts["thumbnails"] += 1
    else:
        pixels = placeholder_pixels(entry["kind"]).ravel()
        width = height = PLACEHOLDER_SIZE
        counts["failed" if entry["thumbnail"] else "placeholders"] += 1
    preview = scene.preview_ensure()
    preview.image_size = (width, height)
    preview.image_pixels_float.foreach_set(pixels)
    return scene


def write_folder(folder: dict, out_dir: str, catalog_ids: dict, counts: dict) -> None:
    scenes = [make_standin(entry, catalog_ids, counts) for entry in folder["entries"]]
    final = os.path.join(out_dir, folder["blend"])
    # Written beside the target and renamed, so an interrupted build never leaves a
    # half-written file under the name the library scans.
    temp = os.path.join(out_dir, ".tmp-" + folder["blend"])
    bpy.data.libraries.write(temp, set(scenes), fake_user=True, compress=True)
    os.replace(temp, final)
    counts["assets"] += len(scenes)
    for scene in scenes:
        bpy.data.scenes.remove(scene)


def main(job_path: str) -> None:
    with open(job_path, "r", encoding="utf-8") as fh:
        job = json.load(fh)
    started = time.time()
    counts = {"assets": 0, "thumbnails": 0, "placeholders": 0, "failed": 0}
    folders = job["folders"]
    for done, folder in enumerate(folders, 1):
        write_folder(folder, job["out_dir"], job["catalog_ids"], counts)
        print(f"POSER_PROGRESS {done} {len(folders)}", flush=True)
    summary = {"folders": len(folders), "seconds": round(time.time() - started, 2),
               "blender": bpy.app.version_string, **counts}
    print("POSER_DONE " + json.dumps(summary), flush=True)


if __name__ == "__main__":
    try:
        main(sys.argv[sys.argv.index("--") + 1])
    except Exception as exc:                       # reported to the parent, then a non-zero exit
        traceback.print_exc()
        print(f"POSER_ERROR {type(exc).__name__}: {exc}", flush=True)
        sys.exit(1)
