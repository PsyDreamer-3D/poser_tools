# SPDX-License-Identifier: GPL-3.0-or-later

import bmesh


def remove_loose_verts(obj):
    """Remove vertices not connected to any edge (Poser seam vertices)."""
    bm = bmesh.new()
    bm.from_mesh(obj.data)
    loose = [v for v in bm.verts if not v.link_edges]
    if loose:
        bmesh.ops.delete(bm, geom=loose, context='VERTS')
        bm.to_mesh(obj.data)
        obj.data.update()
    bm.free()


def remove_unused_material_slots(obj):
    """Remove material slots not referenced by any polygon.

    Direct-API equivalent of bpy.ops.object.material_slot_remove_unused().
    mesh.materials.pop(index=i) shifts down the material_index of every polygon
    pointing past slot i, so popping the unused slots from the highest index
    down leaves the remaining face assignments correct.
    """
    mesh = obj.data
    n_mats = len(mesh.materials)
    if n_mats <= 1:
        return

    face_mat = [0] * len(mesh.polygons)
    mesh.attributes['material_index'].data.foreach_get('value', face_mat)
    used = set(face_mat)

    for i in reversed(range(n_mats)):
        if i not in used:
            mesh.materials.pop(index=i)
    mesh.update()


def sort_material_slots_by_face_order(obj):
    """Reorder material slots so they match the order materials first appear in polygon sequence.

    Poser organizes polygons by body part (head first, torso next, etc.), so sorting
    slots by first-face occurrence restores the expected body-part grouping.
    """
    mesh = obj.data
    n_mats = len(mesh.materials)
    if n_mats <= 1:
        return

    mat_attr = mesh.attributes['material_index'].data
    face_mat = [0] * len(mesh.polygons)
    mat_attr.foreach_get('value', face_mat)

    # Find the first polygon index at which each slot is used.
    first_face = [len(face_mat)] * n_mats
    for face_idx, mat_idx in enumerate(face_mat):
        if first_face[mat_idx] == len(face_mat):
            first_face[mat_idx] = face_idx

    # sorted_old[new_slot] = old_slot
    sorted_old = sorted(range(n_mats), key=lambda i: first_face[i])

    if sorted_old == list(range(n_mats)):
        return

    old_to_new = [0] * n_mats
    for new_idx, old_idx in enumerate(sorted_old):
        old_to_new[old_idx] = new_idx

    remapped = [old_to_new[i] for i in face_mat]
    mat_attr.foreach_set('value', remapped)

    old_mats = list(mesh.materials)
    for new_idx, old_idx in enumerate(sorted_old):
        mesh.materials[new_idx] = old_mats[old_idx]

    mesh.update()


