import bpy
from .functionsArmature import rename_all_bones, prefix_bones


class OT_RenameArmatureBones_Operator(bpy.types.Operator):
    bl_idname = "poser.rename_armature_bones"
    bl_label = "Rename Armature Bones"

    @classmethod
    def poll(cls, context):
        if context.active_object is None or context.active_object.type != 'ARMATURE':
            return False

        return True

    def execute(self, context):
        rename_all_bones(context.active_object)
        return {'FINISHED'}


class OT_PrefixArmatureBones_Operator(bpy.types.Operator):
    bl_idname = "poser.prefix_armature_bones"
    bl_label = "Prefix Armature Bones"

    @classmethod
    def poll(cls, context):
        if context.active_object is None or context.active_object != 'ARMATURE':
            return False

        return True

    def execute(self, context):
        prefix_bones(context.active_object)
        return {'FINISHED'}
