# SPDX-License-Identifier: GPL-3.0-or-later

import bpy
import re
from .functionsArmature import rename_all_bones, rename_bone, delete_body_bone
from .functionsWeightGroups import strip_trailing_digits


def get_top_level_bones(bones):
    top_level_bones = []

    for bone in bones:
        # Poser exports parent-level bones as Body
        if re.search('Body', bone.name):
            top_level_bones.append(bone.name)

    return top_level_bones


def suggest_primary_root(armature):
    """Return the name of the most likely primary root bone, or None."""
    bones = armature.data.bones
    body_roots = get_top_level_bones(bones)

    if not body_roots:
        return None
    if len(body_roots) == 1:
        return body_roots[0]

    # Heuristic 1: prefer the bone with no numeric suffix.
    # Blender appends .001, .002 to later duplicates, so the un-suffixed name
    # is almost always the first-imported (main) figure.
    no_suffix = [n for n in body_roots if not re.search(r'\.[0-9]{3}$', n)]
    if len(no_suffix) == 1:
        return no_suffix[0]

    # Heuristic 2: most descendants → most complex skeleton → main figure.
    return max(body_roots, key=lambda n: len(bones[n].children_recursive))


def select_bone(obj, name):
    obj.data.edit_bones[name].select = True
    obj.data.edit_bones[name].select_head = True
    obj.data.edit_bones[name].select_tail = True


def deselect_bone(_obj, name):
    _obj.data.edit_bones[name].select = False
    _obj.data.edit_bones[name].select_head = False
    _obj.data.edit_bones[name].select_tail = False


def separate_armatures(figure_name, _obj):
    # Caller must have _obj active and in Edit Mode.
    # Exits with _obj active in Edit Mode.
    while True:
        bones = _obj.data.bones
        parents = get_top_level_bones(bones)
        remaining = [p for p in parents if p != figure_name]
        if not remaining:
            break

        target = remaining[0]

        for bone in list(_obj.data.edit_bones):
            deselect_bone(_obj, bone.name)

        select_bone(_obj, target)
        for child in _obj.data.bones[target].children_recursive:
            select_bone(_obj, child.name)

        bpy.ops.armature.separate()
        # After separate(): new armature is active, Object Mode.
        # Restore _obj in Edit Mode for the next iteration.
        bpy.context.view_layer.objects.active = _obj
        bpy.ops.object.mode_set(mode='EDIT')


def strip_trailing_digits_from_bones(obj):
    bones = obj.data.bones
    for bone in bones:
        new_name = strip_trailing_digits(bone.name)
        if new_name != "":
            obj.data.bones[bone.name].name = new_name


def reparent_conforming_meshes(main_armature, conforming_armatures):
    """Re-parent conforming-figure meshes to the main armature.

    Searches all scene objects rather than relying on obj.parent being
    set correctly after armature.separate(), which varies by Blender version.
    """
    conf_arm_set = set(conforming_armatures)
    for obj in bpy.data.objects:
        if obj.type != 'MESH':
            continue
        if obj.parent in conf_arm_set:
            world_matrix = obj.matrix_world.copy()
            obj.parent = main_armature
            obj.matrix_parent_inverse = main_armature.matrix_world.inverted()
            obj.matrix_world = world_matrix
        for mod in obj.modifiers:
            if mod.type == 'ARMATURE' and mod.object in conf_arm_set:
                mod.object = main_armature


def rename_conforming_vertex_groups(conforming_armatures, scene_objects):
    """
    For each separated conforming armature:
      - Strip trailing numeric suffixes from all mesh vertex groups in the scene.
        Primary meshes are unaffected (their groups have no suffix).
      - Strip suffixes from the conforming armature's bone names.
      - Delete the non-deforming Body root bone from the conforming armature.
    Conforming armature objects are kept in the scene.
    """
    for obj in scene_objects:
        if obj.type != 'MESH':
            continue
        for vg in obj.vertex_groups:
            new_name = strip_trailing_digits(vg.name)
            if new_name != vg.name:
                vg.name = new_name

    for arm_obj in conforming_armatures:
        strip_trailing_digits_from_bones(arm_obj)

        # delete_body_bone() requires Edit Mode with arm_obj as active.
        # Temporarily unhide in case it was hidden before processing.
        was_hidden = arm_obj.hide_viewport
        arm_obj.hide_viewport = False
        bpy.context.view_layer.objects.active = arm_obj
        bpy.ops.object.mode_set(mode='EDIT')
        delete_body_bone(arm_obj)
        bpy.ops.object.mode_set(mode='OBJECT')
        arm_obj.hide_viewport = was_hidden



