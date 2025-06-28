import bpy
from .functions import consolidate_poser_shapekeys


class ConsolidatePoserShapeKeys:
    def consolidate_poser_shapekeys(self):
        pass


class OT_FixPoserShapekeys_Operator(ConsolidatePoserShapeKeys, bpy.types.Operator):
    bl_idname = "poser.fix_poser_shapekeys"
    bl_label = "Fix Poser Shapekeys"
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        if context.active_object.type != 'MESH':
            return False

        return True

    def execute(self, context):
        obj = context.active_object
        shapekeys = obj.data.shape_keys.key_blocks
        options = context.scene.poser_shapekeys_addon
        ConsolidatePoserShapeKeys.consolidate_poser_shapekeys()
        # consolidate_poser_shapekeys(obj, shapekeys)
        return {'FINISHED'}
