# SPDX-License-Identifier: GPL-3.0-or-later

from .poserToolsAddonSettings import PoserShapeKeysAddon_Settings
from .poserToolsPreferences import PoserRuntimePathItem, PoserToolsPreferences

classes = (
    PoserShapeKeysAddon_Settings,
    PoserRuntimePathItem,
    PoserToolsPreferences,
)


# What the one Poser Library asset shelf shows. Blender shows a single shelf per 3D View region
# and has no switcher between shelves that poll alike (probed on 5.3: three shelves, only the first
# ever appeared), so the kinds share one shelf and this picks between them. "All" is the default
# (owner, 2026-11-18). Per session, not saved in files.
SHELF_KINDS = (
    ('all', "All", "Every package in the library"),
    ('injection', "Injection", "Morph packages that add shape keys"),
    ('removal', "Removal", "Packages that remove a package's shape keys"),
    ('material', "MAT Poses", "Material presets"),
)


def _redraw_viewports(_self, context):
    for window in context.window_manager.windows:
        for area in window.screen.areas:
            if area.type == 'VIEW_3D':
                area.tag_redraw()


def register():
    import bpy
    for cls in classes:
        bpy.utils.register_class(cls)
    bpy.types.Scene.poser_shapekeys_addon = bpy.props.PointerProperty(type=PoserShapeKeysAddon_Settings)
    bpy.types.WindowManager.poser_shelf_kind = bpy.props.EnumProperty(
        name="Shelf Shows", items=SHELF_KINDS, default='all', update=_redraw_viewports,
        description="Which Poser Library packages the asset shelf lists")


def unregister():
    import bpy
    if hasattr(bpy.types.WindowManager, "poser_shelf_kind"):
        del bpy.types.WindowManager.poser_shelf_kind
    if hasattr(bpy.types.Scene, "poser_shapekeys_addon"):
        del bpy.types.Scene.poser_shapekeys_addon
    for cls in reversed(classes):
        bpy.utils.unregister_class(cls)
