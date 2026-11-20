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
path. The one exception is mac_pict.py beside it, itself stdlib only, imported by putting this
file's own folder on sys.path. The parent (core/library_build.py) plans the work, writes the catalog file and
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

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import mac_pict  # noqa: E402  (beside this file; see the module docstring)

# A preview larger than this only costs file size; the shelf draws previews small.
PREVIEW_MAX = 256
PLACEHOLDER_SIZE = 128
# Flat tiles by kind, with a glyph so they read apart even without colour: a plus for
# injection, a minus for removal, a filled swatch for a MAT pose or material collection (violet), an outlined
# swatch with one filled quarter for a single material (green: the glyph alone was too subtle at shelf size to
# tell an .mt5 from an .mc6, owner 2026-11-19). Blender can't draw text onto an
# image in a background process, and the shelf prints the name under the tile anyway. The same
# tile, small, is the badge on every real thumbnail (owner, 2026-11-18): an INJ, its REM and a
# character's MAT often ship the same picture, and the shelf lists every kind together by default.
_TILE_COLORS = {"injection": (0.17, 0.36, 0.58), "removal": (0.58, 0.38, 0.17),
                "material": (0.42, 0.24, 0.55), "slot_material": (0.20, 0.48, 0.28)}
_DEFAULT_TILE = (0.35, 0.35, 0.35)
# Badge edge as a share of the thumbnail's shorter side; Poser thumbnails are ~91 px, so ~29 px.
_BADGE_SHARE = 0.32


def kind_tile(kind: str, n: int) -> np.ndarray:
    """An n x n RGBA float tile for `kind`: its colour, a lighter rim and a white glyph."""
    color = np.array(_TILE_COLORS.get(kind, _DEFAULT_TILE), dtype=np.float32)
    tile = np.empty((n, n, 4), dtype=np.float32)
    tile[..., :3] = color
    tile[..., 3] = 1.0
    rim = max(1, round(n * 3 / 128))
    light = np.minimum(color + 0.18, 1.0)
    tile[:rim, :, :3] = tile[-rim:, :, :3] = tile[:, :rim, :3] = tile[:, -rim:, :3] = light
    bar, half = max(1, n // 10), n // 2
    glyph = np.array([0.95, 0.95, 0.95], dtype=np.float32)
    lo, hi = n // 4, n - n // 4
    if kind == "material":
        tile[lo:hi, lo:hi, :3] = glyph                                      # a swatch
        return tile
    if kind == "slot_material":
        # A single material: one quarter of the swatch, as it changes one of an object's materials.
        tile[lo:half, lo:half, :3] = glyph
        tile[lo:hi, lo:lo + bar, :3] = tile[lo:hi, hi - bar:hi, :3] = glyph
        tile[lo:lo + bar, lo:hi, :3] = tile[hi - bar:hi, lo:hi, :3] = glyph
        return tile
    tile[half - bar // 2:half + bar // 2 + 1, lo:hi, :3] = glyph          # horizontal bar: both kinds
    if kind != "removal":
        tile[lo:hi, half - bar // 2:half + bar // 2 + 1, :3] = glyph      # vertical bar too: a plus
    return tile


def placeholder_pixels(kind: str) -> np.ndarray:
    """A PLACEHOLDER_SIZE square RGBA float tile for `kind`."""
    return kind_tile(kind, PLACEHOLDER_SIZE)


def add_kind_badge(pixels, width: int, height: int, kind: str) -> np.ndarray:
    """`pixels` (flat RGBA, Blender's bottom-up rows) with the kind tile in the top-right corner."""
    image = np.array(pixels, dtype=np.float32).reshape(height, width, 4)
    size = max(12, round(min(width, height) * _BADGE_SHARE))
    if size + 2 > min(width, height):
        return image.ravel()                 # too small to mark without hiding the picture
    margin = max(1, round(min(width, height) * 0.03))
    top = height - margin                    # row 0 is the bottom of the image
    image[top - size:top, width - margin - size:width - margin] = kind_tile(kind, size)
    return image.ravel()


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


def load_rsr_thumbnail(path: str):
    """(width, height, flat float32 RGBA) for the PICT in an old Mac `.rsr`, or None if it can't be read.

    Values are the decoded 8-bit colours / 255, like a loaded image's pixels, so previews from the
    two sources look alike. PICT rows run top to bottom, Blender's bottom to top.
    """
    try:
        width, height, rgb = mac_pict.decode_rsr(path)
    except (OSError, mac_pict.PictError):
        return None
    image = np.frombuffer(bytes(rgb), dtype=np.uint8).reshape(height, width, 3)[::-1]
    pixels = np.ones((height, width, 4), dtype=np.float32)
    pixels[..., :3] = image / 255.0
    return width, height, pixels.ravel()


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

    thumbnail = entry["thumbnail"]
    if not thumbnail:
        loaded = None
    elif thumbnail.lower().endswith(".rsr"):
        loaded = load_rsr_thumbnail(thumbnail)
    else:
        loaded = load_thumbnail(thumbnail)
    if loaded is not None:
        width, height, pixels = loaded
        pixels = add_kind_badge(pixels, width, height, entry["kind"])
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
