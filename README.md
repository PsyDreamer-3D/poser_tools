# Poser Tools Blender Add-on

A collection of tools to make working with rigged figures exported from Poser as FBX files easier in Blender.

---

## Requirements

- Blender 4.4.0 or later
- Licensed under [GPL-3.0-or-later](https://spdx.org/licenses/GPL-3.0-or-later.html)

---

## Installation

To get updates inside Blender, add `https://github.com/PsyDreamer-3D/poser_tools/releases/latest/download/index.json` as a remote repository (**Preferences → Get Extensions → Repositories → + → Add Remote Repository**) and turn on **Check for Updates on Startup**. Releases carry the `index.json` that URL points at, so each new version shows up as an update.

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
- **Legacy Daz3D Figure** *(Auto-Detect)* — Millennium 3 / 4 figures (Michael 4, Victoria 4, Aiko 3, …) name their morphs with a `p`/`PBM` prefix instead of Blender's numeric suffix. The importer works that out from the imported shape-key names, so you normally leave this alone. Choose **Force On** or **Force Off** only if detection guesses wrong; the decision is logged in the **Poser Shapekey Report**.

### 2. Fix Poser Shapekeys *(only if needed)*

Consolidation already ran on import. Use this button to **re-run** it, or to consolidate a mesh imported some other way. The same **Legacy Daz3D Figure** dropdown applies (Auto-Detect by default).

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

### 7. Morph Injection *(optional)*

Adds the morphs from a 3rd-party Poser morph package (an "injection" `.pz2`, the kind sold for Aiko 3, Victoria 4 and similar figures) to an already-imported mesh as new shape keys. These are morphs that were never in the FBX, because the content wasn't loaded in Poser when you exported.

**One-time setup.** In **Edit → Preferences → Add-ons → Poser Tools**, add your Poser content folders under **Poser Runtime Folders**. Each one is the folder that *contains* a `Runtime` folder. **Import Runtime Roots From Poser** reads them from Poser's own `LibraryPrefs.xml`. Add every library your packages draw from: a package often lives in one library and names files in another (a product's entry in a custom folder, the figure's morph files under `Base Figures`), and files in a folder that isn't listed can't be found.

**Apply Morph Injection** (sidebar → **Morph Injection**): select the figure's mesh and click it, then pick the figure (its CR2) and the injection package from the searchable lists built from your Runtime folders. The figure's reference `.obj` is found through the CR2, so there's nothing else to browse for. Without Runtime folders set up, you can still browse to the package `.pz2` and the figure's reference `.obj` by hand.

- Matching the package to your mesh takes about 30 seconds on a 70,000-vertex figure. Blender stays usable, the cursor shows a percentage, and **Esc** cancels.
- Each morph becomes a shape key named after its label in the package (for example `BrowHeavy`), resting at 0.
- Joint-corrective morphs and ones already on the mesh are skipped. A **Poser Morph Injection Report** text block lists what happened, including any files the package names that couldn't be found.
- Packages that keep their data in a separate binary `.pmd` file aren't supported yet.

**Remove Morph Injection** undoes a package by deleting its shape keys. Pick the same package, or its matching `RemDeltas.*` file. No reference `.obj` is needed.

---

## Features

- **Custom FBX importer** with Poser-specific post-import corrections baked in
- **Conforming figure separation** — hair and clothing figures are split into their own armatures automatically, with meshes re-parented to the main figure's armature
- **Shapekey consolidation** — merges Poser's split parent/child morphs into single shapekeys; supports both standard Poser figures and legacy Daz3D Millennium 3/4 figures, detected automatically
- **Morph injection** — add 3rd-party Poser morph packages to an imported figure as shape keys, and remove them again; package files are found across all your Poser Runtime folders
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
