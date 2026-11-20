# SPDX-License-Identifier: GPL-3.0-or-later

import bpy

from ..core.utils import _TAB
import textwrap


class FixPoserShapekeys:
    bl_idname = "VIEW_3D_PT_FixPoserShapekeys"
    bl_label = "Fix Poser Shapekeys"
    bl_category = _TAB
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"


class FixPoserShapekeys_Panel(FixPoserShapekeys, bpy.types.Panel):
    bl_idname = "VIEW_3D_PT_FixPoserShapekeys_Panel"
    bl_label = "Fix Poser Shapekeys"

    def draw(self, context):
        layout = self.layout
        scene = context.scene
        options = scene.poser_shapekeys_addon

        layout.label(text="Runs automatically on import — use this to re-run", icon='INFO')

        row = layout.row(align=True)
        row.prop(options, "legacy_daz_mode")
        text = """
        Legacy Daz figure naming (Millennium 3/4, but not Genesis or
        newer) is auto-detected from the imported shape-key names.
        Override only if detection guessed wrong.
        """
        for line in text.splitlines():
            line = line.strip()
            for chunk in textwrap.wrap(line, 160):
                multiline = layout.row(align=True)
                multiline.alignment = 'EXPAND'
                multiline.label(text=chunk)

        row = layout.row()
        row.scale_y = 1.5

        row.operator("poser.fix_poser_shapekeys")  # defined in operators/fixPoserShapekeys.py
