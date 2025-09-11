import bpy


class ImportPoserFBX_Panel(bpy.types.Panel):
    bl_idname = "VIEW_3D_PT_ImportPoserFBXPanel"
    bl_label = "Import Poser FBX"
    bl_category = "Poser"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"

    def draw(self, context):
        layout = self.layout
        op_row = layout.row(align=True)
        op_row.scale_y = 1.5

        op = op_row.operator("import_scene.fbx", icon="POSE_HLT")
        op.use_anim = False
        op.use_custom_normals = False
        op.force_connect_children = True
        op.automatic_bone_orientation = True

