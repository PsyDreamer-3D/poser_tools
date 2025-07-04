import bpy
from functions.armatureFunctions import rename_all_bones


class OT_RenameArmatureBones_Operator(bpy.types.Operator):
    bl_idname = "poser.rename_armature_bones"
    bl_label = "Rename Armature Bones"

    @classmethod
    def poll(cls, context):
        if context.scene.armature.type != 'ARMATURE':
            return False

        return True

    def execute(self, context):
        rename_all_bones(context.scene.armature)
        return {'FINISHED'}
