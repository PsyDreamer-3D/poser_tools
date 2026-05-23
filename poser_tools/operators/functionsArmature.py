import bpy


def rename_all_bones(armature):
    bones = armature.data.bones

    for bone in bones:
        new_name = rename_bone(bone.name)
        if new_name != "":
            armature.data.bones[bone.name].name = new_name


def rename_bone(name):
    if "root" in name:
        return ""

    if name.find('Left_') != -1:
        return name[5:] + '.L'

    if name.find('Right_') != -1:
        return name[6:] + '.R'

    if name.find('l', 0, 1) != -1:
        name = name[1:] + '.L'
    elif name.find('r', 0, 1) != -1:
        name = name[1:] + '.R'

    return name


def prefix_bones(armature, prefix="DEF-"):
    bones = armature.data.bones

    for bone in bones:
        new_name = rename_bone(bone.name)
        if new_name != "":
            armature.data.bones[bone.name].name = prefix + new_name


def fix_camera_target_bones(armature):
    """
    Remove Poser camera-target bones and fix the bones they distorted.

    Poser's FBX export includes a Face_Camera LimbNode positioned at world origin
    (a Poser camera-aim target that has no deformation role).  Blender's
    force_connect_children averages ALL children when computing a parent's tail
    position and bone_size, so Face_Camera distorts:
      - Head bone tail  (averaged toward world origin)
      - Eye bone tails  (inflated because they inherit the wrong bone_size)
    Must be called while the armature is in Edit Mode.
    """
    edit_bones = armature.data.edit_bones

    parents_to_fix = set()
    for b in list(edit_bones):
        if 'camera' in b.name.lower():
            if b.parent:
                parents_to_fix.add(b.parent.name)
            edit_bones.remove(b)

    for pname in parents_to_fix:
        bone = edit_bones.get(pname)
        if bone is None or not bone.children:
            continue
        child_heads = [c.head.copy() for c in bone.children]
        new_tail = child_heads[0].copy()
        for h in child_heads[1:]:
            new_tail += h
        new_tail /= len(child_heads)
        if (new_tail - bone.head).magnitude > 1e-4:
            bone.tail = new_tail

        correct_size = sum(
            (c.head - bone.head).magnitude for c in bone.children
        ) / len(bone.children)
        for child in bone.children:
            if child.children:
                continue
            direction = child.tail - child.head
            length = direction.magnitude
            if length > correct_size * 1.5 and length > 1e-4:
                child.tail = child.head + direction.normalized() * correct_size


def center_neck_bone_tail(armature):
    """Set the neck bone's tail X to 0. Must be called while the armature is in Edit Mode."""
    for bone in armature.data.edit_bones:
        if 'neck' in bone.name.lower():
            bone.tail.x = 0.0
            return True
    return False


def delete_body_bone(armature):
    """Delete the non-deforming Body bone if present."""
    body = armature.data.edit_bones.get('Body')
    if body is not None:
        armature.data.edit_bones.remove(body)


def _get_side(name):
    """Return 'L', 'R', or None based on naming conventions."""
    lower = name.lower()
    if 'right' in lower or lower.endswith('.r'):
        return 'R'
    if 'left' in lower or lower.endswith('.l'):
        return 'L'
    # Poser single-letter prefix: lEye, rFoot, etc.
    if len(name) > 1 and name[0] == 'r' and name[1].isupper():
        return 'R'
    if len(name) > 1 and name[0] == 'l' and name[1].isupper():
        return 'L'
    return None



_SPINE_KEYWORDS = ('head', 'neck', 'chest', 'abdomen', 'hip')


def recalculate_bone_rolls(armature):
    """Recalculate roll for all bones toward Global +Z, then zero-out spine bones."""
    bpy.ops.armature.select_all(action='SELECT')
    bpy.ops.armature.calculate_roll(type='GLOBAL_POS_Z')
    for bone in armature.data.edit_bones:
        if any(kw in bone.name.lower() for kw in _SPINE_KEYWORDS):
            bone.roll = 0.0
