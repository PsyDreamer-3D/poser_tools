# Changelog

All notable changes to this project will be documented here.
Format based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

---

## [Unreleased]

### Added

- **Apply Morph Injection** reuses the vertex-matching step it spent ~30 seconds on. The result is saved on the mesh, so applying a second package (or one more morph) to the same unchanged mesh finishes in under a second instead of repeating that step each time. A **Vertex correspondence:** line in the **Poser Morph Injection Report** says whether it was reused or rebuilt; editing the mesh or choosing a different reference `.obj` triggers a rebuild automatically, and an unreadable saved copy is reported and rebuilt. The saved data adds about 600 KB to the `.blend`.
- **Apply Morph Injection** (**Morph Injection** panel): adds the morphs from a 3rd-party Poser injection package (`.pz2`, including orchestrators that `readScript` several files) to the active mesh as new shape keys — morphs that were never in the FBX export. Poser's vertex order differs from the FBX-imported mesh, so the figure's reference `.obj` is matched to the mesh by position. New shape keys are named after the morph's label in the package (e.g. `BrowHeavy`, not `PBMDC_39`) and rest at value 0. JCM morphs and ones already on the mesh are skipped, and a **Poser Morph Injection Report** text block lists the result and total time. The matching step takes ~30s on a 70k-vertex figure; Blender stays responsive, the cursor shows a counting percentage, and Esc cancels.
- **Remove Morph Injection**: deletes the shape keys a package added. Point it at either the original `InjDeltas.*` file or its `RemDeltas.*` counterpart; no reference `.obj` is needed. Apply now records what it created on the mesh (`mesh["poser_injected_morphs"]`) because `RemDeltas.*` files carry no usable names.
- **Poser Runtime folders** in the add-on preferences (Edit > Preferences > Add-ons > Poser Tools). Add them by hand or click **Import Runtime Roots From Poser** to read them from Poser's `LibraryPrefs.xml`. The Apply and Remove popups then offer searchable **Figure (CR2)** and **Injection Package** dropdowns; picking a figure finds its reference `.obj` through the CR2's `figureResFile`, so there's no manual `.obj` browsing. The manual path fields still work.
- Releases include an `index.json`, so the add-on can be added in Blender as a remote repository (`https://github.com/PsyDreamer-3D/poser_tools/releases/latest/download/index.json`) and Blender offers updates. Replaces `scripts/generate_index.py`, whose index Blender refused to sync (no `schema_version` or `archive_size`); the release workflow now uses Blender's own `extension server-generate`.

### Changed
- **Requires Blender 5.0.1 or later** (was 4.4). Blender 4.4 and 4.5 users should stay on 1.0.1, the last release that supports them. The add-on is tested on 5.0.1, 5.2 LTS and 5.3, not on 4.x, and the planned thumbnail library builds on Asset Shelf APIs that first appeared in 5.0.
- Added `core/cr2/` — a CR2 (Poser Character Resource) parser adapted from the now-unmaintained `cr2_importer` project, with a bug fixed (channel names containing spaces, e.g. `Blink Right`, were silently dropped). Groundwork for morph injection and for cross-referencing morph names against the source `.cr2`. First `pytest` suite in the repo (`tests/`), covering this parser.
- `core/cr2/name_match.py`: resolves a morph name to its full set of per-actor CR2 channels (a name almost never identifies a single channel — Poser splits a body-wide morph across every actor it touches), trying `internal_name` then `display_name`. Also supplies the human-readable names Apply Morph Injection gives its shape keys.
- Shape-key consolidation now runs **automatically on import** (new **Consolidate Shape Keys** option in the Import Poser FBX dialog, on by default). **Legacy Daz3D Figure** (Millennium 3/4 figures, whose morphs use a `p`/`PBM` name prefix) is now **Auto-Detect** by default, read from the imported shape-key names, with **Force On**/**Force Off** to override; the decision is logged in the report. The same dropdown is on the **Fix Poser Shapekeys** panel. The standalone **Fix Poser Shapekeys** button is now for re-running or for meshes imported another way.
- **Fix Poser Shapekeys** now deletes joint-corrective morphs (JCMs, `JCM…`). FBX export bakes the shape but drops the bone-driven relationship that fires it, so a JCM shape key on the imported mesh can't function — it was only cluttering the list (156 of 196 keys on LaFemme). The report lists every one removed.
- **Fix Poser Shapekeys** now flags morphs whose split child deltas overlap on the same vertex (they get summed, which can over-shoot). Poser's exports haven't shown this in practice, but a malformed one is now reported instead of silently baked in.
- **Fix Poser Shapekeys** now records what it merged as JSON on the mesh (`mesh["poser_shapekey_merges"]`) — which shape keys absorbed which children, and any renames — so a later tool can map a pre-merge morph name back to the surviving one.
- Shape-key consolidation now reports through the Blender status bar and a **Poser Shapekey Report** text block instead of `print()` (invisible on Linux). Running **Fix Poser Shapekeys** on a mesh with no shape keys now shows an error message instead of failing with a traceback.
- Repository restructured to match the `blender-addon-scaffold` conventions: shared helpers moved to a `core/` package, `panels/` → `ui/` and `settings/` → `properties/`, each subpackage owns its own `register()`/`unregister()`, and the top-level `__init__.py` is orchestration only. SPDX headers on every source file; LF line endings throughout. No user-facing behavior change.
- `recalculate_bone_rolls` and `remove_unused_material_slots` now use the direct data API instead of `bpy.ops`.
- Release scripts and the GitHub Actions workflow re-synced with the scaffold template (package directory auto-detected; `BLENDER_VERSION` pinned in one place).

### Known issues
- Blender 5.1.2 can crash importing a multi-figure FBX (hair or clothing alongside the figure). This is an upstream bug in `BM_mesh_bm_from_me()`, [fixed in Blender 5.2](https://projects.blender.org/blender/blender/pulls/156302) and not backported; `poser_tools` has no workaround.

### Fixed
- **Remove Morph Injection** works from `RemDeltas.DC_40_EarDeer` and `RemDeltas.DC_42_EarFeline`, which name a different channel than their `InjDeltas.*` files (the only 2 of 2,305 Inj/Rem pairs on a real content drive that do) and so removed nothing. When a removal file matches nothing on the mesh, Remove falls back to the same-named `InjDeltas.*` file next to it and says so in the report.
- **Apply Morph Injection** names a shape key after the morph's main body, not whichever actor the package lists first. Packages that list a small partial actor first (e.g. `InjDeltas.DC_40_EarDeer.pz2`: neck `pEarDeer`, then head `EarDeer`) produced a `p`-prefixed key; the name now comes from the channel carrying the most deltas. Existing shape keys keep their old names and still work with **Remove Morph Injection**.
- **Apply Morph Injection** no longer silently loads nothing from morph packages whose `readScript` paths differ in case from the folders on disk (e.g. `:Runtime:Libraries:Pose:` vs. `libraries/pose`), which is routine in real Poser content and fatal on case-sensitive filesystems (Linux). One real package, `! All Head Morphs.pz2`, resolved 1 of 257 files and applied none of its morphs; it now resolves all of them. The same fix lets a figure's reference `.obj` be found when its CR2 path differs in case.
- **Apply** and **Remove Morph Injection** now look for a package's referenced files in every Poser Runtime folder added under Preferences, not only the one the package sits in. Many packages live in one library and name files in another (a product's entry in a custom folder, its figure's deltas under `Base Figures`), and those references were never found, even with the folder added. When none of a package's files can be found, the error now says so and points at the Runtime-folder preference instead of reporting "no morphs found".
- **Prefix Armature Bones** button is no longer permanently greyed out — its `poll()` compared the active object to a string instead of checking `.type == 'ARMATURE'`.

---

## [1.0.1] - 2026-05-26
### Fixed
- Imported multi-figure FBX files with conforming hair and/or clothing now correctly handle the separated hair/clothing armatures, preventing errors during import and ensuring proper bone alignment and vertex group assignment.

## [1.0.0] - 2026-05-26

### Added
- Bundled legacy Blender FBX importer (`io_scene_fbx`) to support Poser-specific axis and bone handling that the current Blender importer no longer provides
- Progress indicator cursor during long-running operations
- Conforming figure separation: hair and clothing armatures are split from the main figure into their own armatures, their meshes are re-parented to the main armature, and the separated armatures are hidden from the viewport

### Fixed
- FBX importer now correctly handles leaf bones; eyes and toes are excluded from leaf-bone alignment to prevent incorrect orientations
- Several bugs in the shape-key consolidation process
- Add-on reloading compatibility with Blender 5.0 and newer
- Bundled FBX importer compatibility issues

### Changed
- Material slot order is corrected after import to restore Poser's original body-part grouping
- Post-import cleanup now runs automatically: loose (seam) vertices and unused material slots are removed
- Neck bone X position is zeroed on import
- Shape-key correction process improved for reliability
- Multi-figure FBX files (main figure with conforming hair and/or clothing) handled more robustly
- Codebase restructured to follow Blender extension conventions
- N-panel tab renamed to **Poser FBX Importer**

---

## [0.1.0] - 2026-03-24

### Added
- GitHub Actions release workflow for automated builds and publishing
- `bump_version.py` script for version management
- `strip_dev_blocks.py` script to remove development-only code blocks before a release build

### Changed
- Manifest file cleaned up and finalized
- Large codebase refactor (formatting and style consistency across all modules)

---

## Initial Development — 2025-06-28 to 2026-02-21

### Added
- Import Poser FBX operator with Poser-specific post-import armature and geometry corrections
- Bone alignment to Global +Z and bone roll recalculation after import
- Batch bone renaming: `Left_`/`Right_` prefixes and `lBone`/`rBone` camelCase prefixes converted to Blender's `.L`/`.R` suffix convention
- Batch bone prefixing (e.g. `DEF-`)
- Batch vertex group renaming to stay in sync with renamed bones
- Batch vertex group prefixing
- Shapekey consolidation operator: merges Poser's split parent/child morphs into single usable shapekeys; includes legacy Daz3D Millennium 3/4 figure support (`p`-prefix morphs)
- Operator to remove loose (seam) vertices left over from Poser's geometry
- N-panel UI in the 3D Viewport sidebar (tab: **Poser Tools**)
- Add-on reload support
