# SPDX-License-Identifier: GPL-3.0-or-later

import bpy


class POSER_UL_injection_morphs(bpy.types.UIList):
    def draw_item(self, context, layout, data, item, icon, active_data, active_propname, index):
        row = layout.row(align=True)
        label = row.row()
        label.enabled = not item.locked
        label.prop(item, "selected", text=item.name)
        if item.status:
            row.label(text=item.status)
