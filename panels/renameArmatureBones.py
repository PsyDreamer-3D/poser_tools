import bpy


class RenameArmatureBones(bpy.types.Panel):
    bl_idname = "VIEW_3D_PT_RenameArmatureBones"
    bl_label = "Rename Armature Bones"
    bl_category = "Poser"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"


class RenameArmatureBones_Panel(RenameArmatureBones, bpy.types.Panel):
    bl_label = "Rename Armature Bones"
    bl_idname = "VIEW_3D_PT_RenameArmatureBones"

    def draw(self, context):
        layout = self.layout
        layout.row().label(text="Add .L/.R suffixes to symmetrical bones")

        row = layout.row()
        row.scale_y = 1.5
        row.operator("poser.rename_armature_bones")

