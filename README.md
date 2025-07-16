Poser Tools Blender Add-on
---

Still in development so use at your own risk!

This add-on is a collection of tools designed to make working with rigged models exported from Poser as fbx files easier.

### Features:
- One-click setup that aligns the bones to the Global +Z axis and sets the armature to x-ray and In Front.
- FBX importer that automatically aligns bones and connect children on import.
- Batch renaming bones and vertex groups to fix Blender's naming conventions
  - Replacing `Left_` or `Right_` prefixes with `.R` or `.L` suffixes. This makes it easier to convert the base rig to CloudRig or Rigify
- Batch fix imported shapekeys so that they're easier to work with.

### Planned features:
- Fix double material groups imported from Poser.

### Future Ideas:
- Import Poser CR2 file directly instead of using FBX.
