# SPDX-License-Identifier: GPL-3.0-or-later

import os

import bpy
from bpy.props import CollectionProperty, IntProperty, StringProperty

# Package-relative so this resolves correctly whether Blender loaded
# poser_tools as a legacy add-on or a namespaced extension -- never hardcode
# "poser_tools" here.
_ADDON_PKG = __package__.rsplit('.', 1)[0]


class PoserRuntimePathItem(bpy.types.PropertyGroup):
    """One registered Poser Runtime root -- the folder that *contains* a
    Runtime folder, same convention core/cr2/poser_paths.py already uses."""
    path: StringProperty(
        name="Runtime Root",
        description="Folder that contains a Poser Runtime directory",
        subtype='DIR_PATH',
    )
    label: StringProperty(
        name="Label",
        description="Optional friendly name shown in the path list (leave blank to use the folder name)",
        default="",
    )


class PoserToolsPreferences(bpy.types.AddonPreferences):
    bl_idname = _ADDON_PKG

    runtime_paths: CollectionProperty(type=PoserRuntimePathItem)
    runtime_paths_index: IntProperty(default=0)

    def draw(self, context):
        layout = self.layout

        layout.label(text="Poser Runtime Folders:")
        row = layout.row()
        row.template_list(
            "POSER_UL_runtime_paths", "runtime_paths",
            self, "runtime_paths",
            self, "runtime_paths_index",
        )
        col = row.column(align=True)
        col.operator("poser.runtime_root_add", icon='ADD', text="")
        col.operator("poser.runtime_root_remove", icon='REMOVE', text="")
        col.separator()
        op_up = col.operator("poser.runtime_root_move", icon='TRIA_UP', text="")
        op_up.direction = "UP"
        op_down = col.operator("poser.runtime_root_move", icon='TRIA_DOWN', text="")
        op_down.direction = "DOWN"

        if self.runtime_paths and 0 <= self.runtime_paths_index < len(self.runtime_paths):
            sel = self.runtime_paths[self.runtime_paths_index]
            layout.prop(sel, "path")
            layout.prop(sel, "label", placeholder="Label (optional)")
            if sel.path and not os.path.isdir(sel.path):
                warn = layout.row()
                warn.alert = True
                warn.label(text="Path not found", icon='ERROR')

        layout.separator()
        layout.operator("poser.runtime_roots_import_from_poser", icon='IMPORT')


def get_runtime_roots(context) -> list:
    """Return the list of registered Runtime root paths from add-on preferences."""
    try:
        prefs = context.preferences.addons[_ADDON_PKG].preferences
        return [item.path for item in prefs.runtime_paths if item.path]
    except (KeyError, AttributeError):
        return []
