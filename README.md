# Poser Tools Blender Add-on

A collection of tools to make working with rigged figures exported from Poser as FBX files easier in Blender.

---

## Requirements

- Blender 5.0.1 or later
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

- A package with several morphs opens a **Choose Morphs to Apply** list next: tick the ones you want (everything new is ticked to start), use the filter box to find one, **Select All** / **Select None**, and the box underneath estimates the time and shape-key memory for your selection. Morphs that can't be applied are greyed out with the reason (already on the mesh, joint-corrective, `.pmd`, no deltas). Only the files for the morphs you tick are read, so a few morphs out of a 250-morph package load in about a second instead of nine. A package that holds a single morph skips the list.
- Matching the package to your mesh takes about 30 seconds on a 70,000-vertex figure, the first time. Blender stays usable the whole run, reading a big package and adding its shape keys included; the cursor shows a percentage, the status bar says what it's doing (reading the package, matching, building morphs 40/259), and **Esc** cancels, taking back any shape keys it had already added. The result is saved on the mesh (about 600 KB in the `.blend`), so applying more packages to the same, unedited mesh skips that step; editing the mesh or picking a different reference `.obj` redoes it.
- Each morph becomes a shape key named after its label in the package (for example `BrowHeavy`), resting at 0, unless the package dials it. Character packages (a "MOR INJ" that loads its morphs and then sets them, like Lady Xtaci's) have their values applied, including to morphs that were already on the mesh, so you get the character, not just the morphs. A slider widens when a value is negative or above 1. Applying the same package again sets the values again. Anything else such a package sets, like a body scale or a pose, isn't applied and is listed in the report.
- Joint-corrective morphs and ones already on the mesh are skipped. A **Poser Morph Injection Report** text block lists what happened, including any files the package names that couldn't be found.
- Packages that keep their data in a separate binary `.pmd` file aren't supported yet.

**Remove Morph Injection** undoes a package by deleting its shape keys. Pick the same package, or its matching `RemDeltas.*` file. No reference `.obj` is needed.

**MAT Poses** (sidebar → **MAT Poses** → **Apply MAT Pose**): applies a Poser MAT pose, the `.pz2` files that only set materials, to the selected figure's materials. Pick it from your Runtime library or browse to it; before you click **Apply**, the dialog says how many of the mesh's materials it will replace and lists, by name, the file's materials with no match on this mesh and the mesh's materials the file leaves alone.

- Materials are matched by name: the FBX import names each one `<object>_<material>` (`Figure_SkinTorso`), and the MAT's `SkinTorso` is matched to it. A MAT written for another figure applies to whatever names it shares (a Victoria 3 MAT on Aiko 3 sets 31 of 33; Aiko 3 has no `SkinLeg`).
- The first time a MAT replaces a material, the original is kept in the file as `<name> (original)`. Ctrl+Z undoes the whole MAT.
- With the **Package Library** built, MAT poses are in the Asset Browser too (every MAT in your Runtime folders, figure, clothing, hair and prop MATs alike, violet tiles where there's no thumbnail) and on the asset shelf (switch it to **MAT Poses**): double-click one, right-click it → **Apply MAT Pose to Active Mesh...**, or click it on the shelf to open the same dialog with the file already picked.
- Textures are found across all your Runtime folders, the way Poser finds them: paths written relative to `Runtime` or `Runtime:Textures`, absolute Windows paths from the author's machine, Poser `.bum` bump files (their source image is used) and, last, a file of the same name anywhere in your Runtimes. The report lists every texture found that way. Classic MATs (colour plus texture, bump and transparency maps) become a Principled BSDF. Node-based Poser materials are translated node by node; Poser nodes Blender has no equivalent for here, such as the toon node, are left out and named in the **Poser MAT Pose Report**, along with any texture it couldn't find.

**Package Library** (Edit → Preferences → Add-ons → Poser Tools, below the Runtime folders): **Build Library Index** scans your Runtime folders for morph injection and removal packages and MAT poses (body poses are left out, including ones that also inject morphs) and registers them as a Blender asset library called **Poser**; change the name with **Library Name** in the same section (a name another library already uses gets Blender's `.001`). Open an Asset Browser, pick that library, and you get your packages with their Poser thumbnails, filed by Runtime and then by folder as in Poser's Library palette (a Runtime's **Label** in the list above names it, else its folder name), searchable by name and tagged `injection` / `removal` (and `incomplete` when a file the package names can't be found). Thumbnails that exist only as an old Mac `.rsr` file are decoded too. Packages with no thumbnail at all get a coloured tile: blue with a plus for injection, amber with a minus for removal, violet for a MAT pose.

- The first build reads your whole library and takes up to a minute on a slow drive (about 15 s once the files are cached); Blender stays usable, the cursor shows a percentage, and **Esc** cancels without changing anything. **Update Library Index** afterwards takes a couple of seconds, because only changed folders are rewritten. **Full Rebuild** starts over. Rebuild after you change the Runtime folders (the panel tells you) or install new content.
- Nothing is written into your Poser folders. The index lives in Blender's config folder for the version you built it in, so each Blender version builds its own.
- Building adds the library to Blender's asset library list; **Remove Library Entry** takes it out again (the index files stay, and the next build re-adds it).
- **Using a package:** select your mesh, then double-click a package in the Asset Browser, or right-click it and choose **Apply to Active Mesh...** or **Remove from Active Mesh** (the same button is in the Asset Browser's side panel, under the asset's details). In the 3D Viewport, **View → Asset Shelf** (Object Mode, mesh selected) opens the Poser Library shelf. It lists everything by default; narrow it to **Injection**, **Removal** or **MAT Poses** in the shelf's settings popover (the icon at its right end) or in the sidebar's **Poser Library Shelf** panel, then click a tile. Every thumbnail carries a corner badge for its kind (blue + injection, amber − removal, violet swatch MAT pose), since an INJ, its REM and a character's MAT often share one picture. Injection opens **Choose Morphs to Apply** directly once the mesh has had a package applied (it remembers the figure); the first time, Apply's popup opens with the package already picked so you only choose the figure. Removal happens straight away and Ctrl+Z undoes it. Dragging a package onto the viewport does nothing: Blender gives add-ons no way to handle that drop.

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
