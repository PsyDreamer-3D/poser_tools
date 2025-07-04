import bpy
import textwrap


class FixPoserShapekeys(bpy.types.Panel):
    bl_idname = "VIEW_3D_PT_FixPoserShapekeys"
    bl_label = "Fix Poser Shapekeys"
    bl_category = "Poser"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"


class FixPoserShapekeys_Panel(FixPoserShapekeys, bpy.types.Panel):
    bl_idname = "VIEW_3D_PT_FixPoserShapekeys_Panel"
    bl_label = "Fix Poser Shapekeys"

    def draw(self, context):
        layout = self.layout
        scene = context.scene
        options = scene.poser_shapekeys_addon

        row = layout.row(align=True)
        row.prop(options, "is_daz")
        text = """
        Example of legacy Daz figures:
        Millennium 4, Millennium 3, etc.,
        but not Genesis or newer.
        """
        for line in text.splitlines():
            line = line.strip()
            for chunk in textwrap.wrap(line, 160):
                multiline = layout.row(align=True)
                multiline.alignment = 'EXPAND'
                multiline.label(text=chunk)

        row = layout.row()
        row.scale_y = 1.5

        row.operator("poser.fix_poser_shapekeys")  # defined in operators/fixPoserShapekeys.py
