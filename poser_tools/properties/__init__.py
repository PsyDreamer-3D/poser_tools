# SPDX-License-Identifier: GPL-3.0-or-later

from .poserToolsAddonSettings import PoserShapeKeysAddon_Settings

classes = (
    PoserShapeKeysAddon_Settings,
)


def register():
    import bpy
    for cls in classes:
        bpy.utils.register_class(cls)
    bpy.types.Scene.poser_shapekeys_addon = bpy.props.PointerProperty(type=PoserShapeKeysAddon_Settings)


def unregister():
    import bpy
    if hasattr(bpy.types.Scene, "poser_shapekeys_addon"):
        del bpy.types.Scene.poser_shapekeys_addon
    for cls in reversed(classes):
        bpy.utils.unregister_class(cls)
