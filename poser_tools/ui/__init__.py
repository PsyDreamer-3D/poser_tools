# SPDX-License-Identifier: GPL-3.0-or-later

from .applyMorphInjection import ApplyMorphInjection_Panel
from .fixPoserShapekeys import FixPoserShapekeys_Panel
from .importPoserFBX import ImportPoserFBX_Panel
from .prefixArmatureBones import PrefixArmatureBones_Panel
from .prefixWeightGroups import PrefixWeightGroups_Panel
from .renameArmatureBones import RenameArmatureBones_Panel
from .renameWeightGroups import RenameWeightGroups_Panel
from .runtimePaths import POSER_UL_runtime_paths

classes = (
    POSER_UL_runtime_paths,
    ImportPoserFBX_Panel,
    FixPoserShapekeys_Panel,
    ApplyMorphInjection_Panel,
    RenameArmatureBones_Panel,
    PrefixArmatureBones_Panel,
    RenameWeightGroups_Panel,
    PrefixWeightGroups_Panel,
)


def register():
    import bpy
    for cls in classes:
        bpy.utils.register_class(cls)


def unregister():
    import bpy
    for cls in reversed(classes):
        bpy.utils.unregister_class(cls)
