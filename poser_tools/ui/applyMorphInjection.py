# SPDX-License-Identifier: GPL-3.0-or-later

import bpy

from ..core.utils import _TAB


class ApplyMorphInjection_Panel(bpy.types.Panel):
    bl_idname = "VIEW_3D_PT_ApplyMorphInjection_Panel"
    bl_label = "Apply Morph Injection"
    bl_category = _TAB
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"

    def draw(self, context):
        layout = self.layout

        layout.label(text="Import a 3rd-party morph package (.pz2) onto the active mesh", icon='INFO')

        row = layout.row()
        row.scale_y = 1.5
        row.operator("poser.apply_morph_injection")  # defined in operators/applyMorphInjection.py
