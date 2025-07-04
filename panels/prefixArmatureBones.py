import bpy


class PrefixArmatureBones_Panel(bpy.types.Panel):
    bl_label = "Prefix Armature Bones"
    bl_idname = "VIEW_3D_PT_PrefixArmatureBones"
    bl_category = "Poser"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"

    def draw(self, context):
        layout = self.layout

        scene = context.scene
        options = scene.poser_shapekeys_addon
        text_field_row = layout.row(align=True)
        text_field_row.prop(options, "bone_prefix")

        button_row = layout.row()
        button_row.scale_y = 1.5
        button_row.operator("poser.prefix_armature_bones")
