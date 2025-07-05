import bpy
from .functionsArmature import rename_all_bones, rename_bone
from .functionsWeightGroups import strip_trailing_digits


def select_bone(obj, name):
    obj.data.edit_bones[name].select = True
    obj.data.edit_bones[name].select_head = True
    obj.data.edit_bones[name].select_tail = True


def separate_armatures(figure_name, obj):
    bones = obj.data.bones
    for bone in bones:
        if bone.name == figure_name:
            select_bone(obj, bone.name)

            for child in bone.children_recursive:
                select_bone(obj, child.name)

            # separate into new armature here
            bpy.ops.armature.separate()


def strip_trailing_digits_from_bones(obj):
    bones = obj.data.bones
    for bone in bones:
        new_name = strip_trailing_digits(bone.name)
        if new_name != "":
            obj.data.bones[bone.name].name = new_name


def setup_poser_figure(figure_name, objects):
    bpy.context.space_data.show_restrict_column_viewport = True

    for obj in objects:
        if obj.type != 'ARMATURE':
            continue

        # do something to armature here?
        obj.show_in_front = True
        obj.display_type = 'WIRE'

        separate_armatures(figure_name, obj)
        strip_trailing_digits_from_bones(obj)
        rename_all_bones(obj)
