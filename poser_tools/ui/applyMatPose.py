# SPDX-License-Identifier: GPL-3.0-or-later

import bpy

from ..core.utils import _TAB


class ApplyMatPose_Panel(bpy.types.Panel):
    bl_idname = "VIEW_3D_PT_ApplyMatPose_Panel"
    bl_label = "Materials"
    bl_category = _TAB
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"

    def draw(self, context):
        layout = self.layout
        # Both defined in operators/: applyMatPose.py and applyMaterialToSlot.py.
        layout.label(text="MAT pose or collection (.pz2, .mc6): by name, to the mesh", icon='INFO')
        row = layout.row()
        row.scale_y = 1.5
        row.operator("poser.apply_mat_pose")
        layout.label(text="Single material (.mt5): to the active slot", icon='INFO')
        row = layout.row()
        row.scale_y = 1.5
        row.operator("poser.apply_material_to_slot")
