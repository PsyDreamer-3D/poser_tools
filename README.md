# Poser Tools Blender Add-on

A collection of tools to make working with rigged figures exported from Poser as FBX files easier in Blender.

---

## Requirements

- Blender 4.4.0 or later
- Licensed under [GPL-3.0-or-later](https://spdx.org/licenses/GPL-3.0-or-later.html)

---

## Installation

1. Download the latest release `.zip` from the Releases page.
2. In Blender, go to **Edit → Preferences → Add-ons**.
3. Click **Install from Disk…** and select the downloaded `.zip`.
4. Enable the **Poser Tools** add-on from the list.

Alternatively, drag-and-drop the `.zip` directly into the Blender window.

---

## How to Use

All tools are found in the **3D Viewport sidebar** (press `N` to open it) under the **Poser FBX Importer** tab.

The recommended workflow, in order:

### 1. Import Poser FBX

Click **Import Poser FBX** and select your `.fbx` file. The importer is based on Blender's legacy FBX import implementation and applies a series of Poser-specific corrections automatically:

- Fixes the `Face_Camera` camera-target bone that Poser generates
- Centers the neck bone on the X axis
- Aligns terminal bones (fingertips, toe tips, thumbs) to their parent chain
- Recalculates bone rolls toward Global +Z
- Removes loose (seam) vertices left over from Poser's geometry
- Removes unused material slots and restores Poser's original body-part material order
- Detects conforming figures (hair, clothing) and separates them into their own armatures, with their meshes re-parented to the main armature
- **Consolidates shape keys** — merges Poser's parent/child morph pairs into single usable sliders and removes joint-corrective (JCM) morphs. What it merged is recorded on the mesh, and a full log is written to a **Poser Shapekey Report** text block.

Two shape-key options in the import dialog:

- **Consolidate Shape Keys** *(on by default)* — untick to import the raw shape keys and run the consolidation later by hand.
- **Legacy Daz3D Figure** *(off)* — tick for Millennium 3 / 4 figures (Michael 4, Victoria 4, Aiko 3, …), which name their morphs with a `p`/`PBM` prefix instead of Blender's numeric suffix.

### 2. Fix Poser Shapekeys *(only if needed)*

Consolidation already ran on import. Use this button to **re-run** it, or to consolidate a mesh imported some other way. Set the **Legacy Daz3D Figure** checkbox first for M3/M4 figures.

### 3. Rename Armature Bones

With the armature selected as the active object, click **Rename Armature Bones**.

This converts Poser's `Left_`/`Right_` prefixes and `lBone`/`rBone` camelCase prefixes to Blender's `.L`/`.R` suffix convention, which is required for mirroring and compatibility with Rigify and CloudRig.

### 4. Prefix Armature Bones *(optional)*

Enter a prefix in the text field (default: `DEF-`) and click **Prefix Armature Bones** to batch-add it to every bone in the active armature.

### 5. Rename Weight Groups

With a mesh selected as the active object, click **Rename Weight Groups**.

Applies the same `Left_`/`Right_` → `.L`/`.R` renaming to vertex groups, keeping them in sync with the renamed bones.

### 6. Prefix Weight Groups *(optional)*

Enter a prefix and click **Prefix Weight Groups** to batch-add it to every vertex group on the active mesh.

---

## Features

- **Custom FBX importer** with Poser-specific post-import corrections baked in
- **Conforming figure separation** — hair and clothing figures are split into their own armatures automatically, with meshes re-parented to the main figure's armature
- **Shapekey consolidation** — merges Poser's split parent/child morphs into single shapekeys; supports both standard Poser figures and legacy Daz3D Millennium 3/4 figures
- **Bone renaming** — converts Poser naming conventions to Blender's `.L`/`.R` symmetry standard
- **Weight group renaming** — keeps vertex groups in sync with renamed bones
- **Batch prefixing** — add a prefix (e.g. `DEF-`) to all bones or vertex groups in one click

---

## Out of scope

Weight-mapped / TriAx rigging support is permanently out of scope. The Poser/DAZ ecosystem already
solves that problem, and reimplementing it here would be a worse version of existing tooling. This
add-on stays focused on cleaning up FBX-imported figures (bones, weight groups, shape keys, materials).

## Notes

This add-on is still in active development — use at your own risk.

For auto-rigging Poser figures in Blender, see the companion add-on [Poser Autorigger](https://github.com/jesgs/poser_autorigger).
