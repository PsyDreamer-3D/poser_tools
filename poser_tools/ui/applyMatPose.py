# SPDX-License-Identifier: GPL-3.0-or-later

import bpy

from ..core.utils import _TAB


class ApplyMatPose_Panel(bpy.types.Panel):
    bl_idname = "VIEW_3D_PT_ApplyMatPose_Panel"
    bl_label = "MAT Poses"
    bl_category = _TAB
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"

    def draw(self, context):
        layout = self.layout
        layout.label(text="Apply a Poser MAT pose (.pz2) to the active mesh's materials", icon='INFO')
        row = layout.row()
        row.scale_y = 1.5
        row.operator("poser.apply_mat_pose")  # defined in operators/applyMatPose.py
