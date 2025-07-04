import bpy
from .functionsShapeKeys import consolidate_poser_shapekeys


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
        if obj["morphs_consolidated"]:
            print('morphs are already consolidated')
            return {'CANCELLED'}

        shapekeys = obj.data.shape_keys.key_blocks
        scene = context.scene
        options = scene.poser_shapekeys_addon

        consolidate_poser_shapekeys(obj, shapekeys, options.is_daz)
        obj["morphs_consolidated"] = True
        print('morphs have been consolidated')
        return {'FINISHED'}
