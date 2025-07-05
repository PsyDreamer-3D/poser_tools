import bpy
from .functionsPoserFigure import setup_poser_figure


class OT_SetupPoserFigure_Operator(bpy.types.Operator):
    bl_idname = "poser.setup_poser_figure"
    bl_label = "Setup Poser Figure"
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        if len(context.selected_objects) == 0:
            return False

        return True

    def execute(self, context):
        setup_poser_figure(context.selected_objects)
        return {'FINISHED'}
