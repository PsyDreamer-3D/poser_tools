import bpy
import re
from .functionsArmature import rename_all_bones, rename_bone
from .functionsWeightGroups import strip_trailing_digits


def get_top_level_bones(bones):
    top_level_bones = []

    for bone in bones:
        # Poser exports parent-level bones as Body
        if re.search('Body', bone.name):
            top_level_bones.append(bone.name)

    return top_level_bones


def select_bone(obj, name):
    obj.data.edit_bones[name].select = True
    obj.data.edit_bones[name].select_head = True
    obj.data.edit_bones[name].select_tail = True


def deselect_bone(_obj, name):
    _obj.data.edit_bones[name].select = False
    _obj.data.edit_bones[name].select_head = False
    _obj.data.edit_bones[name].select_tail = False


def separate_armatures(figure_name, _obj):
    bones = _obj.data.bones
    parents = get_top_level_bones(bones)

    # this is dumb and I shouldn't have to do this
    for bone in bones:
        deselect_bone(_obj, bone.name)

    for parent in parents:
        if parent == figure_name:
            continue

        select_bone(_obj, parent)

        if len(_obj.data.bones[parent].children_recursive) > 0:
            for child in _obj.data.bones[parent].children_recursive:
                select_bone(_obj, child.name)

        # separate into new armature here
        bpy.ops.armature.separate()


def strip_trailing_digits_from_bones(obj):
    bones = obj.data.bones
    for bone in bones:
        new_name = strip_trailing_digits(bone.name)
        if new_name != "":
            obj.data.bones[bone.name].name = new_name


def setup_poser_figure(figure_name, objects):
    # Before deselecting everything, apply scale/rotation
    # Poser's scale is 1/100 smaller than Blender, plus rotation is different as well
    bpy.ops.object.transform_apply(location=False, rotation=True, scale=True)

    bpy.ops.object.select_all(action='DESELECT')

    for obj in objects:
        if obj.type == 'MESH':
            # check if mesh is parented to an armature
            if obj.parent.type == 'ARMATURE':
                # go into edit mode, select all loose geometry and delete it
                bpy.ops.object.editmode_toggle()
                bpy.ops.mesh.select_loose()
                bpy.ops.mesh.delete(type='VERT')
                bpy.ops.object.editmode_toggle()
            continue

        if obj.type == 'ARMATURE':
            # maybe we could also change display to b-bone or stick?
            obj.show_in_front = True
            obj.display_type = 'WIRE'

            bpy.ops.object.editmode_toggle()  # go into edit mode

            # change bone-roll to Global +Z to prevent issues later on
            bpy.ops.armature.select_all(action='SELECT')
            bpy.ops.armature.calculate_roll(type='GLOBAL_POS_Z')

            separate_armatures(figure_name, obj)
            strip_trailing_digits_from_bones(obj)
            rename_all_bones(obj)

            bpy.ops.object.editmode_toggle()  # we're done here
            continue


