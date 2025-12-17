Poser Tools Blender Add-on
---

Still in development so use at your own risk!

This add-on is a collection of tools designed to make working with rigged models exported from Poser as fbx files easier.

### Features:
- FBX importer that automatically aligns bones and connect children on import.
- One-click setup that aligns the bones to the Global +Z axis and sets the armature to x-ray and In Front.
- Batch renaming bones and vertex groups to fix Blender's naming conventions
  - Replacing `Left_` or `Right_` prefixes with `.R` or `.L` suffixes. This makes it easier to convert the base rig to CloudRig or Rigify
- Batch fix imported shapekeys so that they're easier to work with.

### Known Issues/Feature Ideas:
- CHANGE: Change armature correction—remove Global +Z reset. This is now part of [Poser Autorigger](https://github.com/jesgs/poser_autorigger)
- CHANGE: Remove bone renaming from armature correction process. This is now part of [Poser Autorigger](https://github.com/jesgs/poser_autorigger)
- BUG: **This may be resolved by Blender 5.0**. Last digit of each finger bone isn't aligned with the rest of the chain. Selecting the last bone, then bone behind it, and then pressing CTRL+ALT+A aligns the last with the rest of the chain. The last bone of the thumb chain needs to be handled differently.
- BUG: The Shapekey consolidation function sometimes misses shapekeys—this was discovered with certain morphs in the Stephanie3-to-Aiko3 figure.
- BUG: Related to above. Morphs prefixed with "PBM" also need to be treated as child morphs when dealing with Daz figures (namely Mil 3 or possibly 4). Some work has been started but it needs to be completed.
- BUG: Occasionally, there is no full-body morph for corresponding "p" morphs. This could be the result of user-error when exporting from Poser, but these morphs should be tracked and they need to be treated the same as parent morphs.
- IMPROVEMENT: Include JCM morphs when consolidating. Currently, the consolidation process ignores these shape-keys but doesn't delete them.
- IMPROVEMENT: Change shapekey min and max values to -1 and 1.
- IMPROVEMENT: Material groups imported from Poser are doubled—with the duplicated group being empty. Currently testing a script that uses the [Bmesh module](https://docs.blender.org/api/current/bmesh.html) to check if the material is empty, and then delete it.
- IMPROVEMENT: Separate non-connected meshes into separate objects (eyebrows, eyelashes, eyes, gums/teeth, tongue, and pubic hair mesh).
- IMPROVEMENT: The tail of the neck bone (connected to the root of head bone) is off-center on the x-axis
  ```python
  # This should be run before the Global +Z operator
  # Get the armature object
  armature = bpy.context.active_object
  edit_bones = armature.data.edit_bones
  edit_bones['Head'].head[0] = 0 # this should also move the tail of the neck bone since they're connected
  ```
- IMPROVEMENT: Improve speed of shapekey consolidation.
- ~~IMPROVEMENT: Eye bones are too long and could be shortened.~~ This is already being handled by [Poser Autorigger](https://github.com/jesgs/poser_autorigger).
- ~~FEATURE: Add an auto-rigging feature specific to Poser figures—this will be a separate add-on.~~ See [Poser Autorigger](https://github.com/jesgs/poser_autorigger)
- ~~FEATURE: Import Poser CR2 file directly instead of using FBX.~~ This will be developed as a separate add-on.
