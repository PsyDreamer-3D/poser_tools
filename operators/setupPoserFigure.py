import bpy
from bpy.props import StringProperty
from .functionsPoserFigure import setup_poser_figure


class OT_SetupPoserFigure_Operator(bpy.types.Operator):
    bl_idname = "poser.setup_poser_figure"
    bl_label = "Setup Poser Figure"
    bl_options = {'REGISTER', 'UNDO'}

    figure_name: StringProperty(
        name="Figure Name",
        description="Figure Name",
        default="Figure",
    )

    @classmethod
    def poll(cls, context):
        if len(context.scene.objects) == 0:
            return False

        return True

    def execute(self, context):
        setup_poser_figure(self.figure_name, context.selected_objects)
        return {'FINISHED'}
