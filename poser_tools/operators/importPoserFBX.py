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
    separate_figures: BoolProperty(
        name="Separate Figures",
        description="Separate conforming figures (hair, clothing) into their own armatures "
                    "and rename vertex groups to match the primary armature",
        default=True,
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
        col.prop(self, "separate_figures")

    def invoke(self, context, event):
        context.window_manager.fileselect_add(self)
        return {'RUNNING_MODAL'}

    def execute(self, context):
        from ..vendor.io_scene_fbx import import_fbx
        from .functionsArmature import (
            fix_camera_target_bones,
            center_neck_bone_tail,
            delete_body_bone,
            recalculate_bone_rolls,
            align_terminal_bones_to_parent,
            compute_vertex_group_centroids,
        )
        from .functionsMesh import (
            remove_loose_verts,
            remove_unused_material_slots,
            sort_material_slots_by_face_order,
        )
        from .functionsPoserFigure import (
            suggest_primary_root,
            separate_armatures,
            strip_trailing_digits_from_bones,
            rename_conforming_vertex_groups,
            reparent_conforming_meshes,
        )

        wm = context.window_manager
        wm.progress_begin(0, 100)

        try:
            wm.progress_update(0)
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
            wm.progress_update(40)

            if 'FINISHED' not in result:
                return result

            imported = list(context.selected_objects)
            armature = next((obj for obj in imported if obj.type == 'ARMATURE'), None)
            mesh_objects = [obj for obj in imported if obj.type == 'MESH']

            # Apply Poser's 1/100 scale and axis rotation while everything is still selected.
            bpy.ops.object.transform_apply(location=False, rotation=True, scale=True)

            # --- Mesh corrections ---
            n_meshes = max(len(mesh_objects), 1)
            for i, obj in enumerate(mesh_objects):
                wm.progress_update(40 + int(20 * i / n_meshes))
                remove_loose_verts(obj)
                remove_unused_material_slots(context, obj)
                sort_material_slots_by_face_order(obj)
            wm.progress_update(60)

            # --- Armature corrections ---
            if armature is not None:
                armature.show_in_front = True
                armature.display_type = 'WIRE'

                # Hide any extra armatures that arrived directly from the FBX
                # (e.g. a conforming figure already stored as its own armature object).
                for obj in imported:
                    if obj.type == 'ARMATURE' and obj is not armature:
                        obj.hide_viewport = True

                # Detect primary root before entering Edit Mode so Body* bones are still present.
                figure_name = suggest_primary_root(armature)

                # Compute thumb-tip centroids while still in Object Mode (mesh data accessible).
                centroids = compute_vertex_group_centroids(armature, mesh_objects)
                wm.progress_update(70)

                context.view_layer.objects.active = armature
                bpy.ops.object.mode_set(mode='EDIT')

                fix_camera_target_bones(armature)
                center_neck_bone_tail(armature)
                align_terminal_bones_to_parent(armature, centroids)
                recalculate_bone_rolls(armature)
                wm.progress_update(80)

                armatures_before = {o.name for o in bpy.data.objects if o.type == 'ARMATURE'}

                if figure_name is not None and self.separate_figures:
                    # separate_armatures() exits with armature active in Edit Mode.
                    separate_armatures(figure_name, armature)

                # Delete non-deforming Body root from primary (still in Edit Mode).
                delete_body_bone(armature)
                strip_trailing_digits_from_bones(armature)

                bpy.ops.object.mode_set(mode='OBJECT')
                wm.progress_update(90)

                armatures_after = {o.name for o in bpy.data.objects if o.type == 'ARMATURE'}
                conforming_armatures = [
                    bpy.data.objects[n] for n in (armatures_after - armatures_before)
                ]
                if conforming_armatures:
                    rename_conforming_vertex_groups(
                        conforming_armatures,
                        context.view_layer.objects,
                    )
                    reparent_conforming_meshes(armature, conforming_armatures)
                    for arm_obj in conforming_armatures:
                        arm_obj.hide_viewport = True
                    context.view_layer.objects.active = armature

                armature.name = "Armature"

            wm.progress_update(100)

        finally:
            wm.progress_end()

        return result
