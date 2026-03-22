def rename_all_bones(armature):
    bones = armature.data.bones

    for bone in bones:
        new_name = rename_bone(bone.name)
        if new_name is not "":
            armature.data.bones[bone.name].name = new_name


def rename_bone(name):
    if "root" in name:
        return ""

    if name.find('Left_') is not -1:
        return name[5:] + '.L'

    if name.find('Right_') is not -1:
        return name[6:] + '.R'

    if name.find('l', 0, 1) is not -1:
        name = name[1:] + '.L'
    elif name.find('r', 0, 1) is not -1:
        name = name[1:] + '.R'

    return name


def prefix_bones(armature, prefix="DEF-"):
    bones = armature.data.bones

    for bone in bones:
        new_name = rename_bone(bone.name)
        if new_name is not "":
            armature.data.bones[bone.name].name = prefix + new_name
