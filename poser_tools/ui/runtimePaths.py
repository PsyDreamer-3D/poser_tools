# SPDX-License-Identifier: GPL-3.0-or-later

import bpy


class POSER_UL_runtime_paths(bpy.types.UIList):
    def draw_item(self, context, layout, data, item, icon, active_data, active_propname, index):
        row = layout.row(align=True)
        row.label(text=item.label or item.path or "(empty)")
