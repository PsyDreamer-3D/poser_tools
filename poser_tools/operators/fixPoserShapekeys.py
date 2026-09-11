# SPDX-License-Identifier: GPL-3.0-or-later

import bpy
from ..core.functionsShapeKeys import consolidate_poser_shapekeys
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

        report = consolidate_poser_shapekeys(obj, shapekeys, options.is_daz)
        obj["morphs_consolidated"] = True

        summary = (
            f"Consolidated {len(report['consolidated'])} morph(s), "
            f"kept {len(report['working_kept'])} working, "
            f"skipped {len(report['empty_skipped'])} empty"
        )
        if report['promoted']:
            summary += f", promoted {len(report['promoted'])} orphan(s)"
        if report['jcm_removed']:
            summary += f", removed {len(report['jcm_removed'])} JCM"
        self.report({'INFO'}, summary)

        _write_report(context, _REPORT_TEXT, [summary, ""] + report['log'])
        return {'FINISHED'}
