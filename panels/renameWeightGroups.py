import bpy


class RenameWeightGroupsPanel(bpy.types.Panel):
    bl_label = "Rename Weight Groups"
    bl_idname = "VIEW_3D_PT_RenameWeightGroups"
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = "Poser"


class RenameWeightGroups_Main_Panel(RenameWeightGroupsPanel, bpy.types.Panel):
    bl_label = "Rename Weight Groups"
    bl_idname = "VIEW3D_PT_RenameWeightGroups_Main_Panel"

    def draw(self, context):
        layout = self.layout
        layout.row().label(text="Add .L/.R prefixes to symmetrical weight-groups")

        row = layout.row()
        row.scale_y = 1.5
        row.operator("poser.rename_weight_groups")
