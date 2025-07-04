import bpy
from .functionsWeightGroups import rename_vertex_groups


class OT_RenameWeightGroups_Operator(bpy.types.Operator):
    bl_idname = "poser.rename_weight_groups"
    bl_description = "Rename weight groups to conform to Blender symmetry standards"
    bl_label = "Rename weight groups"

    @classmethod
    def poll(cls, context):
        if context.active_object is None or context.active_object.type != 'MESH':
            return False

        return True

    def execute(self, context):
        rename_vertex_groups(context.active_object)
        return {'FINISHED'}
