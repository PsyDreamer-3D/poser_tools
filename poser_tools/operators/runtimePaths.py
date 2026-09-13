# SPDX-License-Identifier: GPL-3.0-or-later

import os

import bpy
from bpy.props import EnumProperty, StringProperty

from ..core.cr2.poser_library_prefs import find_library_prefs_files, parse_content_folders

_ADDON_PKG = __package__.rsplit('.', 1)[0]


def _prefs(context):
    return context.preferences.addons[_ADDON_PKG].preferences


class POSER_OT_runtime_root_add(bpy.types.Operator):
    """Add a new Poser Runtime root folder"""
    bl_idname = "poser.runtime_root_add"
    bl_label = "Add Runtime Root"

    def execute(self, context):
        prefs = _prefs(context)
        prefs.runtime_paths.add()
        prefs.runtime_paths_index = len(prefs.runtime_paths) - 1
        return {'FINISHED'}


class POSER_OT_runtime_root_remove(bpy.types.Operator):
    """Remove the selected Poser Runtime root folder"""
    bl_idname = "poser.runtime_root_remove"
    bl_label = "Remove Runtime Root"

    def execute(self, context):
        prefs = _prefs(context)
        idx = prefs.runtime_paths_index
        if 0 <= idx < len(prefs.runtime_paths):
            prefs.runtime_paths.remove(idx)
            prefs.runtime_paths_index = max(0, idx - 1)
        return {'FINISHED'}


class POSER_OT_runtime_root_move(bpy.types.Operator):
    """Move the selected Poser Runtime root up or down in the list"""
    bl_idname = "poser.runtime_root_move"
    bl_label = "Move Runtime Root"

    direction: EnumProperty(
        items=[
            ("UP", "Up", "Move entry up"),
            ("DOWN", "Down", "Move entry down"),
        ],
        default="DOWN",
    )

    def execute(self, context):
        prefs = _prefs(context)
        idx = prefs.runtime_paths_index
        paths = prefs.runtime_paths
        new_idx = idx - 1 if self.direction == "UP" else idx + 1
        if 0 <= new_idx < len(paths):
            paths.move(idx, new_idx)
            prefs.runtime_paths_index = new_idx
        return {'FINISHED'}


class POSER_OT_runtime_roots_import_from_poser(bpy.types.Operator):
    """Import registered Runtime roots from Poser's own LibraryPrefs.xml"""
    bl_idname = "poser.runtime_roots_import_from_poser"
    bl_label = "Import Runtime Roots From Poser"

    library_prefs_path: StringProperty(
        name="LibraryPrefs.xml",
        description="Point directly at Poser's LibraryPrefs.xml if it wasn't found automatically",
        subtype='FILE_PATH',
    )

    def invoke(self, context, event):
        found = find_library_prefs_files()
        if found:
            return self.execute(context)
        return context.window_manager.invoke_props_dialog(self, width=500)

    def draw(self, context):
        layout = self.layout
        layout.label(text="Poser's LibraryPrefs.xml wasn't found automatically.", icon='INFO')
        layout.prop(self, "library_prefs_path")

    def execute(self, context):
        xml_paths = find_library_prefs_files()
        if not xml_paths:
            if not self.library_prefs_path or not os.path.isfile(self.library_prefs_path):
                self.report({'ERROR'}, "No LibraryPrefs.xml found or specified.")
                return {'CANCELLED'}
            xml_paths = [self.library_prefs_path]

        prefs = _prefs(context)
        existing = {os.path.normcase(os.path.realpath(item.path))
                    for item in prefs.runtime_paths if item.path}

        resolved_roots = []
        seen = set()
        for xml_path in xml_paths:
            for root in parse_content_folders(xml_path):
                key = os.path.normcase(root)
                if key not in seen:
                    seen.add(key)
                    resolved_roots.append(root)

        added = 0
        already_present = 0
        for root in resolved_roots:
            if os.path.normcase(root) in existing:
                already_present += 1
                continue
            item = prefs.runtime_paths.add()
            item.path = root
            item.label = os.path.basename(root.rstrip("/\\"))
            added += 1

        if added:
            prefs.runtime_paths_index = len(prefs.runtime_paths) - 1

        self.report(
            {'INFO'},
            f"Imported {added} Runtime root(s) from Poser "
            f"({already_present} already present, from {len(xml_paths)} LibraryPrefs.xml file(s))."
        )
        return {'FINISHED'}
