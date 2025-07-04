import bpy


class RenameArmatureBonesPanel(bpy.types.Panel):
    bl_idname = "VIEW3D_PT_RenameArmatureBones"
    bl_label = "Rename Armature Bones"
    bl_category = "Poser"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"


class RenameArmatureBones_Main_Panel(RenameArmatureBonesPanel, bpy.types.Panel):
    bl_label = "Rename Armature Bones"
    bl_idname = "VIEW_3D_PT_RenameArmatureBones"

    def draw(self, context):
        layout = self.layout
        layout.row().label(text="Add .L/.R prefixes to symmetrical bones")

        scene = context.scene
        options = scene.poser_shapekeys_addon
        text_field_row = layout.row(align=True)
        text_field_row.prop(options, "bone_prefix")

        row = layout.row()
        row.scale_y = 1.5
        row.operator("poser.rename_armature_bones")
