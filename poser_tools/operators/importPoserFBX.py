import bpy


class OT_ImportPoserFBX(bpy.types.Operator):
    bl_idname = "poser.import_poser_fbx"
    bl_label = "Import Poser FBX"

    @classmethod
    def poll(cls, context):
        return True  # only temporary

    def execute(self, context):
        self.report({'ERROR'}, 'something executed here')
        return {'FINISHED'}

    def invoke(self, context, event):
        return bpy.ops.import_scene.fbx('INVOKE_DEFAULT', use_anim=False, use_custom_normals=False,
                                        force_connect_children=True, automatic_bone_orientation=True)
