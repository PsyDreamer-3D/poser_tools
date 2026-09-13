# SPDX-License-Identifier: GPL-3.0-or-later

import bpy
from ..core.functionsShapeKeys import consolidate_poser_shapekeys, format_consolidation_summary
from ..core.utils import _write_report

_REPORT_TEXT = "Poser Shapekey Report"


class OT_FixPoserShapekeys_Operator(bpy.types.Operator):
    bl_idname = "poser.fix_poser_shapekeys"
    bl_label = "Fix Poser Shapekeys"
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        if context.active_object is None or context.active_object.type != 'MESH':
            return False

        return True

    def execute(self, context):
        obj = context.active_object

        if ("morphs_consolidated" in obj) and obj["morphs_consolidated"] is True:
            self.report({'INFO'}, "Morphs already consolidated on this mesh.")
            return {'CANCELLED'}

        if obj.data.shape_keys is None:
            self.report({'ERROR'}, "Active mesh has no shape keys.")
            return {'CANCELLED'}

        shapekeys = obj.data.shape_keys.key_blocks
        options = context.scene.poser_shapekeys_addon

        report = consolidate_poser_shapekeys(obj, shapekeys, options.legacy_daz_mode)
        obj["morphs_consolidated"] = True

        summary = format_consolidation_summary(report)

        header = [summary]
        if report['overlaps']:
            n_ov = len(report['overlaps'])
            header.append(f"{n_ov} morph(s) had overlapping child deltas (summed, may over-shoot) — details below")
            self.report(
                {'WARNING'},
                f"{summary}. {n_ov} morph(s) with overlapping child deltas — see the '{_REPORT_TEXT}' text block.",
            )
        else:
            self.report({'INFO'}, summary)

        _write_report(context, _REPORT_TEXT, header + ["", *report['log']])
        return {'FINISHED'}
