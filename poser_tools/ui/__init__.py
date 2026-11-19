# SPDX-License-Identifier: GPL-3.0-or-later

from .applyMatPose import ApplyMatPose_Panel
from .applyMorphInjection import ApplyMorphInjection_Panel
from .fixPoserShapekeys import FixPoserShapekeys_Panel
from .importPoserFBX import ImportPoserFBX_Panel
from .packageAssets import (
    POSER_PT_package_asset,
    VIEW3D_AST_poser_injection_packages,
    VIEW3D_AST_poser_material_packages,
    VIEW3D_AST_poser_removal_packages,
    register_menus,
    unregister_menus,
)
from .pickInjectionMorphs import POSER_UL_injection_morphs
from .prefixArmatureBones import PrefixArmatureBones_Panel
from .prefixWeightGroups import PrefixWeightGroups_Panel
from .renameArmatureBones import RenameArmatureBones_Panel
from .renameWeightGroups import RenameWeightGroups_Panel
from .runtimePaths import POSER_UL_runtime_paths

classes = (
    POSER_UL_runtime_paths,
    POSER_UL_injection_morphs,
    ImportPoserFBX_Panel,
    FixPoserShapekeys_Panel,
    ApplyMorphInjection_Panel,
    ApplyMatPose_Panel,
    RenameArmatureBones_Panel,
    PrefixArmatureBones_Panel,
    RenameWeightGroups_Panel,
    PrefixWeightGroups_Panel,
    POSER_PT_package_asset,
    VIEW3D_AST_poser_injection_packages,
    VIEW3D_AST_poser_removal_packages,
    VIEW3D_AST_poser_material_packages,
)


def register():
    import bpy
    for cls in classes:
        bpy.utils.register_class(cls)
    register_menus()


def unregister():
    import bpy
    unregister_menus()
    for cls in reversed(classes):
        bpy.utils.unregister_class(cls)
