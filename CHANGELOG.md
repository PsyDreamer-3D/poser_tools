# Changelog

All notable changes to this project will be documented here.
Format based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

---

## [Unreleased]

### Changed
- Repository restructured to match the `blender-addon-scaffold` conventions: shared helpers moved to a `core/` package, `panels/` → `ui/` and `settings/` → `properties/`, each subpackage owns its own `register()`/`unregister()`, and the top-level `__init__.py` is orchestration only. SPDX headers on every source file; LF line endings throughout. No user-facing behavior change.
- `recalculate_bone_rolls` and `remove_unused_material_slots` now use the direct data API instead of `bpy.ops`.
- Release scripts and the GitHub Actions workflow re-synced with the scaffold template (package directory auto-detected; `BLENDER_VERSION` pinned in one place).

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
