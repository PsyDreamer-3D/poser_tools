import bpy
import numpy as np
from contextlib import contextmanager


@contextmanager
def _uv_stripped(mesh):
    """Blender 5.1.2 bug #156097 workaround: temporarily remove all UV layers
    so BM_mesh_bm_from_me() has no UV data to mishandle. Remove this function
    (and its callsite) once minimum Blender version is bumped to 5.2."""
    saved_uvs = []
    active_uv_name = None

    if mesh.uv_layers:
        n_loops = len(mesh.loops)
        active = mesh.uv_layers.active
        active_uv_name = active.name if active else None
        for layer in mesh.uv_layers:
            buf = np.empty(n_loops * 2, dtype=np.float32)
            layer.uv.foreach_get("vector", buf)
            saved_uvs.append((layer.name, buf, layer.active_render))
        while mesh.uv_layers:
            mesh.uv_layers.remove(mesh.uv_layers[0])

    try:
        yield
    finally:
        for name, buf, is_render_active in saved_uvs:
            layer = mesh.uv_layers.new(name=name, do_init=False)
            layer.uv.foreach_set("vector", buf)
            if is_render_active:
                layer.active_render = True
        if active_uv_name:
            uv = mesh.uv_layers.get(active_uv_name)
            if uv:
                mesh.uv_layers.active = uv


def remove_loose_verts(obj):
    """Remove vertices not connected to any edge (Poser seam vertices)."""
    ctx = bpy.context
    prev_active = ctx.view_layer.objects.active
    prev_selected = list(ctx.selected_objects)
    for o in prev_selected:
        o.select_set(False)
    obj.select_set(True)
    ctx.view_layer.objects.active = obj

    with _uv_stripped(obj.data):  # bug #156097 workaround, remove for Blender 5.2+
        bpy.ops.object.mode_set(mode='EDIT')
        bpy.ops.mesh.select_all(action='SELECT')
        bpy.ops.mesh.delete_loose(use_verts=True, use_edges=False, use_faces=False)
        bpy.ops.object.mode_set(mode='OBJECT')

    obj.select_set(False)
    for o in prev_selected:
        o.select_set(True)
    ctx.view_layer.objects.active = prev_active


def remove_unused_material_slots(context, obj):
    """Remove material slots not referenced by any face."""
    context.view_layer.objects.active = obj
    bpy.ops.object.material_slot_remove_unused()


def sort_material_slots_by_face_order(obj):
    """Reorder material slots so they match the order materials first appear in polygon sequence.

    Poser organizes polygons by body part (head first, torso next, etc.), so sorting
    slots by first-face occurrence restores the expected body-part grouping.
    """
    mesh = obj.data
    n_mats = len(mesh.materials)
    if n_mats <= 1:
        return
    if 'material_index' not in mesh.attributes:
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
