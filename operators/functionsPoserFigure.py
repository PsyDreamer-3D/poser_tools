import bpy
import re
from .functionsArmature import rename_all_bones, rename_bone
from .functionsWeightGroups import strip_trailing_digits

def setup_poser_figure(objects):
    bpy.context.space_data.show_restrict_column_viewport = True

    for obj in objects:
        if obj.type != 'ARMATURE':
            continue

        # do something to armature here?
        obj.show_in_front = True
        obj.display_type = 'WIRE'

        bones = bpy.context.active_object.data.bones

        for bone in bones:
            if re.search('Body', bone.name):
                print(bone.name, len(bone.children_recursive))
        # separate root bones into separate armatures
        # bpy.ops.outliner.item_activate(extend_range=True, deselect_all=True)
        # bpy.ops.armature.separate()
        # bpy.ops.object.editmode_toggle()
        # bpy.ops.pose.separate_selected_bones()

        rename_all_bones(obj)
        bones = obj.data.bones
        for bone in bones:
            new_name = strip_trailing_digits(bone.name)
            if new_name != "":
                obj.data.bones[bone.name].name = new_name

