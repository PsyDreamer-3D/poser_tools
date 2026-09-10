# SPDX-License-Identifier: GPL-3.0-or-later

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
_THUMB_KEYWORDS = ('thumb',)


def compute_vertex_group_centroids(armature, mesh_objects):
    """Return weighted vertex-group centroids in armature local space.

    Call while in Object Mode before entering Edit Mode. Used to determine the
    correct tail direction for terminal bones whose geometry doesn't follow the
    parent bone's direction (e.g. thumb tips).
    Returns {group_name: centroid_vector}.
    """
    from mathutils import Vector
    arm_mat_inv = armature.matrix_world.inverted_safe()
    centroids = {}

    for mesh_obj in mesh_objects:
        if mesh_obj.type != 'MESH':
            continue
        mesh = mesh_obj.data
        to_arm = arm_mat_inv @ mesh_obj.matrix_world
        group_names = {vg.index: vg.name for vg in mesh_obj.vertex_groups}

        weighted_pos = {}
        for vert in mesh.vertices:
            v_pos = to_arm @ vert.co
            for vge in vert.groups:
                if vge.weight < 0.1:
                    continue
                name = group_names.get(vge.group)
                if name is None:
                    continue
                if name not in weighted_pos:
                    weighted_pos[name] = [Vector((0.0, 0.0, 0.0)), 0.0]
                weighted_pos[name][0] += v_pos * vge.weight
                weighted_pos[name][1] += vge.weight

        for name, (pos_sum, total_w) in weighted_pos.items():
            if total_w < 1e-6:
                continue
            centroid = pos_sum / total_w
            if name in centroids:
                centroids[name] = (centroids[name] + centroid) * 0.5
            else:
                centroids[name] = centroid

    return centroids


def align_terminal_bones_to_parent(armature, centroids=None):
    """Point terminal bones in the same direction as their parent.

    automatic_bone_orientation assigns terminal bones the same correction matrix as
    their parent, but force_connect_children then repositions parent tails based on
    averaged children heads — changing parent direction without updating terminal bone
    tails. This post-import pass corrects that. Call before recalculate_bone_rolls
    so roll recalculation can work from the corrected directions.
    Must be called while the armature is in Edit Mode.

    For thumb terminal bones, aims toward the weighted geometry centroid (from
    centroids dict) instead of the parent direction, since thumb tips curl
    independently of the thumb chain.
    """
    for bone in armature.data.edit_bones:
        if bone.children:
            continue
        if not bone.parent:
            continue

        name_lower = bone.name.lower()
        if 'eye' in name_lower or 'toe' in name_lower:
            continue

        bone_length = (bone.tail - bone.head).magnitude
        is_thumb = any(kw in name_lower for kw in _THUMB_KEYWORDS)

        if is_thumb and centroids and bone.name in centroids:
            direction = centroids[bone.name] - bone.head
            if direction.magnitude > 1e-6:
                if bone_length < 1e-6:
                    bone_length = direction.magnitude
                bone.tail = bone.head + direction.normalized() * bone_length
                continue

        parent_dir = bone.parent.tail - bone.parent.head
        if parent_dir.magnitude < 1e-6:
            continue
        if bone_length < 1e-6:
            bone_length = parent_dir.magnitude
        bone.tail = bone.head + parent_dir.normalized() * bone_length


def recalculate_bone_rolls(armature):
    """Recalculate roll for all bones toward Global +Z, then zero-out spine bones."""
    bpy.ops.armature.select_all(action='SELECT')
    bpy.ops.armature.calculate_roll(type='GLOBAL_POS_Z')
    for bone in armature.data.edit_bones:
        if any(kw in bone.name.lower() for kw in _SPINE_KEYWORDS):
            bone.roll = 0.0
