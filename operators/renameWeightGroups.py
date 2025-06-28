import bpy


class OT_RenameWeightGroups_Operator(bpy.types.Operator):
    bl_idname = "poser.rename_weight_groups"
    bl_label = "Rename weight groups to conform to Blender symmetry standards."
