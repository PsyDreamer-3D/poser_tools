import bpy
from bpy.props import (BoolProperty, StringProperty)


class PoserShapeKeysAddon_Settings(bpy.types.PropertyGroup):
    is_daz: BoolProperty(
        name="Legacy Daz3D Figure?",
        description="Is this a legacy Daz3D Figure?",
        default=False
    )

    weight_group_prefix: StringProperty(
        name="Weight Group Prefix",
        description="Batch-add prefix to weight group names",
        default="DEF-"
    )

    bone_prefix: StringProperty(
        name="Bone Prefix",
        description="Batch-add a prefix to armature bones",
        default="DEF-"
    )

    primary_root_bone: StringProperty(
        name="Primary Root Bone",
        description="Name of primary root bone—the root of the bone that controls the main figure",
        default="Body"
    )