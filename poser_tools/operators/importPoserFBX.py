import bpy
from bpy.props import BoolProperty, EnumProperty, StringProperty


_BONE_AXES = (
    ('X',  "X Axis",  ""),
    ('Y',  "Y Axis",  ""),
    ('Z',  "Z Axis",  ""),
    ('-X', "-X Axis", ""),
    ('-Y', "-Y Axis", ""),
    ('-Z', "-Z Axis", ""),
)


class OT_ImportPoserFBX(bpy.types.Operator):
    """Import a Poser FBX file with settings pre-configured for Poser figures"""
    bl_idname = "poser.import_poser_fbx"
    bl_label = "Import Poser FBX"
    bl_options = {'UNDO'}

    filepath: StringProperty(subtype='FILE_PATH')
    filter_glob: StringProperty(default="*.fbx", options={'HIDDEN'})

    use_anim: BoolProperty(
        name="Import Animation",
        default=False,
    )
    use_custom_normals: BoolProperty(
        name="Custom Normals",
        description="Import custom normals, if available (otherwise Blender will recompute them)",
        default=False,
    )
    ignore_leaf_bones: BoolProperty(
        name="Ignore Leaf Bones",
        description="Ignore the last bone at the end of each chain",
        default=False,
    )
    force_connect_children: BoolProperty(
        name="Force Connect Children",
        description="Force connection of children bones to their parent, "
                    "even if their computed head/tail positions do not match",
        default=True,
    )
    automatic_bone_orientation: BoolProperty(
        name="Automatic Bone Orientation",
        description="Try to align the major bone axis with the bone children",
        default=True,
    )
    primary_bone_axis: EnumProperty(
        name="Primary Bone Axis",
        items=_BONE_AXES,
        default='Y',
    )
    secondary_bone_axis: EnumProperty(
        name="Secondary Bone Axis",
        items=_BONE_AXES,
        default='X',
    )

    def draw(self, context):
        layout = self.layout
        layout.use_property_split = True
        layout.use_property_decorate = False

        layout.prop(self, "use_anim")
        layout.prop(self, "use_custom_normals")

        layout.separator()
        col = layout.column(heading="Armature")
        col.prop(self, "ignore_leaf_bones")
        col.prop(self, "force_connect_children")
        col.prop(self, "automatic_bone_orientation")
        col.prop(self, "primary_bone_axis")
        col.prop(self, "secondary_bone_axis")

    def invoke(self, context, event):
        context.window_manager.fileselect_add(self)
        return {'RUNNING_MODAL'}

    def execute(self, context):
        from ..vendor.io_scene_fbx import import_fbx
        from .functionsArmature import center_neck_bone_tail

        result = import_fbx.load(
            self, context,
            filepath=self.filepath,
            use_anim=self.use_anim,
            use_custom_normals=self.use_custom_normals,
            force_connect_children=self.force_connect_children,
            automatic_bone_orientation=self.automatic_bone_orientation,
            ignore_leaf_bones=self.ignore_leaf_bones,
            primary_bone_axis=self.primary_bone_axis,
            secondary_bone_axis=self.secondary_bone_axis,
            axis_forward='-Z',
            axis_up='Y',
            use_image_search=True,
            use_custom_props=True,
            use_prepost_rot=True,
        )

        if 'FINISHED' not in result:
            return result

        armature = next(
            (obj for obj in context.selected_objects if obj.type == 'ARMATURE'),
            None
        )
        if armature is not None:
            context.view_layer.objects.active = armature
            bpy.ops.object.mode_set(mode='EDIT')
            center_neck_bone_tail(armature)
            bpy.ops.object.mode_set(mode='OBJECT')

        return result
