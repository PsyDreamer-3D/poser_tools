import bpy
from bpy.props import (BoolProperty)


class PoserShapeKeysAddon_Settings(bpy.types.PropertyGroup):
    is_daz: BoolProperty(
        name="Legacy Daz3D Figure?",
        description="Is this a legacy Daz3D Figure?",
        default=False
    )
