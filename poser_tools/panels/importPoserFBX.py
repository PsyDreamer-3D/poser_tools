# SPDX-License-Identifier: GPL-3.0-or-later

import bpy


class ImportPoserFBX_Panel(bpy.types.Panel):
    bl_idname = "VIEW_3D_PT_ImportPoserFBXPanel"
    bl_label = "Import Poser FBX"
    bl_category = "Poser FBX Importer"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"

    def draw(self, context):
        layout = self.layout

        if not hasattr(bpy.types, "POSER_OT_import_poser_fbx"):
            layout.label(text="FBX Importer unavailable", icon="ERROR")
            return

        op_row = layout.row(align=True)
        op_row.scale_y = 1.5
        op_row.operator("poser.import_poser_fbx", icon="POSE_HLT")

