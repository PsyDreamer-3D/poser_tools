# SPDX-License-Identifier: GPL-3.0-or-later

import bpy
from bpy.props import (BoolProperty, EnumProperty, StringProperty)

_LEGACY_DAZ_MODE_ITEMS = (
    ('AUTO', "Auto-Detect", "Sniff the raw shape-key names for the M3/M4 'p'/'PBM' prefix convention"),
    ('ON', "Force On", "Treat as a legacy Daz3D figure (Millennium 3/4) regardless of detection"),
    ('OFF', "Force Off", "Treat as a modern figure (Genesis or newer) regardless of detection"),
)


class PoserShapeKeysAddon_Settings(bpy.types.PropertyGroup):
    legacy_daz_mode: EnumProperty(
        name="Legacy Daz3D Figure",
        description="Millennium 3/4 figures (Michael 4, Victoria 4, Aiko 3, …) use a 'p'/'PBM' "
                    "name prefix instead of Blender's numeric suffix",
        items=_LEGACY_DAZ_MODE_ITEMS,
        default='AUTO',
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

