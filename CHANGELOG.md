# Changelog

All notable changes to this project will be documented here.
Format based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

---

## [Unreleased]

---

## [1.0.3] - 2026-05-31

### Fixed
- Blender 5.1.2 crash (bug #156097) on multi-figure FBX import, occurring on the second or third mesh in the corrections loop. Two crash paths are addressed together:
  - **UV CustomData crash**: `BM_mesh_bm_from_me()` uses a buggy global-vs-per-type index when looking up the active/render UV layer names. Prior edit-mode passes from the FBX importer corrupt the UV `CustomData` structure internally; simply resetting the attribute strings is not sufficient to recover. Fixed by stripping all UV layers before each edit-mode entry and installing a sentinel layer (`__b156097__`) that both attribute strings point to, then fully restoring the original UV data after edit mode exits.
  - **Shape key crash**: `BM_mesh_bm_from_me()` also crashes in its shape key `CustomData` setup on meshes with many shape keys processed after another mesh in the same session. Fixed by temporarily stripping all shape keys before entering edit mode and fully restoring them (with loose-vertex remapping) after exit.
  - The UV strip is the *outer* wrapper and the shape-key strip is the *inner* wrapper so the sentinel is installed before `shape_key_remove(all=True)` fires its depsgraph flush — that flush calls `BM_mesh_bm_from_me` and would crash if the sentinel were not yet in place.

---

## [1.0.2] - 2026-05-30

### Fixed
- Blender 5.1.2 crash when importing a multi-figure FBX file. Caused by a bug in `BM_mesh_bm_from_me()` ([PR #156302](https://projects.blender.org/blender/blender/pulls/156302)) that was not backported to 5.1.2; a fix is included in the upcoming 5.2 LTS release
- Location transform was not being applied after import (only rotation and scale were)

---

## [1.0.1] - 2026-05-26

### Fixed
- Multi-figure FBX import: conforming figures (hair, clothing) could fail to separate correctly in certain file layouts

---

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
