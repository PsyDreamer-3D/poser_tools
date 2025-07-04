import bpy
from bpy.props import PointerProperty
from .panels.fixPoserShapekeys import FixPoserShapekeys_Panel_Main
from .settings.poserToolsAddonSettings import PoserShapeKeysAddon_Settings
from .operators.fixPoserShapekeys import OT_FixPoserShapekeys_Operator

bl_info = {
    "name": "Poser Tools",
    "author": "Jess G",
    "description": "A set of tools to make working with figures imported from Poser easier.",
    "blender": (4, 4, 0),
    "location": "View3D",
    "warning": "",
    "category": "Generic",
    "type": "extension"
}

classes = (
    FixPoserShapekeys_Panel_Main,
    PoserShapeKeysAddon_Settings,
    OT_FixPoserShapekeys_Operator
)


def register():
    for cls in classes:
        bpy.utils.register_class(cls)

    bpy.types.Scene.poser_shapekeys_addon = PointerProperty(type=PoserShapeKeysAddon_Settings)



def unregister():
    for cls in classes:
        bpy.utils.unregister_class(cls)
