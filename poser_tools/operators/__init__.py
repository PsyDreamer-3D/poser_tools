# SPDX-License-Identifier: GPL-3.0-or-later

from .applyMorphInjection import OT_ApplyMorphInjection_Operator, PoserLibraryItem
from .fixPoserShapekeys import OT_FixPoserShapekeys_Operator
from .importPoserFBX import OT_ImportPoserFBX
from .renameArmatureBones import OT_RenameArmatureBones_Operator, OT_PrefixArmatureBones_Operator
from .renameWeightGroups import OT_RenameWeightGroups_Operator, OT_PrefixWeightGroups_Operator
from .runtimePaths import (
    POSER_OT_runtime_root_add,
    POSER_OT_runtime_root_remove,
    POSER_OT_runtime_root_move,
    POSER_OT_runtime_roots_import_from_poser,
)

classes = (
    PoserLibraryItem,
    OT_ImportPoserFBX,
    OT_FixPoserShapekeys_Operator,
    OT_ApplyMorphInjection_Operator,
    OT_RenameArmatureBones_Operator,
    OT_PrefixArmatureBones_Operator,
    OT_RenameWeightGroups_Operator,
    OT_PrefixWeightGroups_Operator,
    POSER_OT_runtime_root_add,
    POSER_OT_runtime_root_remove,
    POSER_OT_runtime_root_move,
    POSER_OT_runtime_roots_import_from_poser,
)


def register():
    import bpy
    for cls in classes:
        bpy.utils.register_class(cls)


def unregister():
    import bpy
    for cls in reversed(classes):
        bpy.utils.unregister_class(cls)
