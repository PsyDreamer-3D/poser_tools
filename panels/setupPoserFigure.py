import bpy


class SetupPoserFigure_Panel(bpy.types.Panel):
    bl_idname = "VIEW_3D_PT_SetupPoserFigurePanel"
    bl_label = "Setup Poser Figure"
    bl_category = "Poser"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"

    def draw(self, context):
        layout = self.layout
        op_row = layout.row(align=True)
        op_row.scale_y = 1.5

        op_row.operator("poser.setup_poser_figure")
