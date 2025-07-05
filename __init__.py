import bpy
from bpy.props import PointerProperty
from .panels.importPoserFBX import ImportPoserFBX_Panel
from .panels.setupPoserFigure import SetupPoserFigure_Panel
from .panels.fixPoserShapekeys import FixPoserShapekeys_Panel
from .panels.renameArmatureBones import RenameArmatureBones_Panel
from .panels.prefixArmatureBones import PrefixArmatureBones_Panel
from .panels.renameWeightGroups import RenameWeightGroups_Panel
from .panels.prefixWeightGroups import PrefixWeightGroups_Panel
from .settings.poserToolsAddonSettings import PoserShapeKeysAddon_Settings
from .operators.fixPoserShapekeys import OT_FixPoserShapekeys_Operator
from .operators.renameArmatureBones import OT_RenameArmatureBones_Operator, OT_PrefixArmatureBones_Operator
from .operators.renameWeightGroups import OT_RenameWeightGroups_Operator, OT_PrefixWeightGroups_Operator
from .operators.importPoserFBX import OT_ImportPoserFBX
from .operators.setupPoserFigure import OT_SetupPoserFigure_Operator

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
    ImportPoserFBX_Panel,
    SetupPoserFigure_Panel,
    FixPoserShapekeys_Panel,
    RenameArmatureBones_Panel,
    PrefixArmatureBones_Panel,
    RenameWeightGroups_Panel,
    PrefixWeightGroups_Panel,
    PoserShapeKeysAddon_Settings,
    OT_FixPoserShapekeys_Operator,
    OT_RenameArmatureBones_Operator,
    OT_PrefixArmatureBones_Operator,
    OT_RenameWeightGroups_Operator,
    OT_PrefixWeightGroups_Operator,
    OT_SetupPoserFigure_Operator
)


def register():
    for cls in classes:
        bpy.utils.register_class(cls)

    bpy.types.Scene.poser_shapekeys_addon = PointerProperty(type=PoserShapeKeysAddon_Settings)



def unregister():
    for cls in classes:
        bpy.utils.unregister_class(cls)
