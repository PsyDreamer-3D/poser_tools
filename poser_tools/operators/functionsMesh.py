import bpy
import numpy as np
from contextlib import contextmanager


_SENTINEL_UV = "__b156097__"


def _find_loose_mask(mesh):
    """Return a boolean numpy array, True for each vertex that has no edges."""
    n_verts = len(mesh.vertices)
    if n_verts == 0:
        return np.zeros(0, dtype=bool)
    used = np.zeros(n_verts, dtype=bool)
    if mesh.edges:
        edge_verts = np.empty(len(mesh.edges) * 2, dtype=np.int32)
        mesh.edges.foreach_get('vertices', edge_verts)
        used[edge_verts] = True
    return ~used


def _build_vertex_remap(loose_mask):
    """Return (new_index, n_new) where new_index[i] is the post-deletion index
    of vertex i, or -1 if vertex i is loose (will be deleted)."""
    cumsum = np.cumsum(~loose_mask).astype(np.int32) - 1
    new_index = np.where(loose_mask, np.int32(-1), cumsum)
    n_new = int((~loose_mask).sum())
    return new_index, n_new


@contextmanager
def _uv_stripped(mesh):
    """Blender 5.1.2 bug #156097 workaround. Remove this function (and its
    callsite) once minimum Blender version is bumped to 5.2.

    BM_mesh_bm_from_me() crashes when active_uv_map_attribute or
    default_uv_map_attribute names a UV layer that doesn't exist in CustomData
    (index -1 is silently promoted to 0, corrupting the layer array).

    Simply resetting the two attribute strings to a valid name is NOT sufficient:
    prior calls to BM_mesh_bm_from_me() (e.g. from the FBX importer's own edit-
    mode passes) may have already corrupted the internal CustomData structure via
    the buggy global-vs-per-type index confusion. That corruption persists after
    exit even if the strings are repaired.

    Fix: strip all real UV layers so BM_mesh_bm_from_me() operates on a clean
    (empty) UV CustomData, then install a sentinel layer and explicitly point
    both attribute strings at it so the name lookup succeeds and finds a valid
    index. After edit mode exits, remove the sentinel and restore the original
    UV layers with their original data and attribute-string assignments.

    IMPORTANT ordering: this context manager must be the OUTER wrapper so that
    the sentinel is installed before _shape_keys_stripped fires
    shape_key_remove(all=True). That operator triggers a depsgraph flush which
    calls BM_mesh_bm_from_me; if the sentinel is not yet in place the stale
    attribute strings cause a crash.
    """
    if not mesh.uv_layers:
        yield
        return

    n_loops = len(mesh.loops)
    saved_uvs = []
    active = mesh.uv_layers.active
    active_uv_name = active.name if active else None

    for layer in mesh.uv_layers:
        buf = np.empty(n_loops * 2, dtype=np.float32)
        layer.uv.foreach_get("vector", buf)
        saved_uvs.append((layer.name, buf, layer.active_render))

    while mesh.uv_layers:
        mesh.uv_layers.remove(mesh.uv_layers[0])

    # Add sentinel and unconditionally point both attribute strings at it so
    # BM_mesh_bm_from_me() finds a valid name in CustomData.
    sentinel = mesh.uv_layers.new(name=_SENTINEL_UV, do_init=True)
    mesh.uv_layers.active = sentinel    # sets active_uv_map_attribute
    sentinel.active_render = True       # sets default_uv_map_attribute

    try:
        yield
    finally:
        s = mesh.uv_layers.get(_SENTINEL_UV)
        if s:
            mesh.uv_layers.remove(s)

        # Restore original UV layers with original data and attribute strings.
        for name, buf, is_render_active in saved_uvs:
            layer = mesh.uv_layers.new(name=name, do_init=False)
            layer.uv.foreach_set("vector", buf)
            if is_render_active:
                layer.active_render = True  # sets default_uv_map_attribute
        if active_uv_name:
            uv = mesh.uv_layers.get(active_uv_name)
            if uv:
                mesh.uv_layers.active = uv  # sets active_uv_map_attribute
        elif mesh.uv_layers:
            mesh.uv_layers.active = mesh.uv_layers[0]


@contextmanager
def _shape_keys_stripped(obj, new_index, n_new_verts):
    """Blender 5.1.2 workaround: temporarily remove shape keys before entering
    edit mode to prevent a crash in BM_mesh_bm_from_me's CustomData setup for
    meshes with many shape keys that have been processed after another mesh in
    the same session. Remove this function (and its callsite) once minimum
    Blender version is bumped to 5.2.

    After edit mode exits, restores shape keys with loose vertices remapped out
    (new_index maps old vertex indices to new; loose vertices map to -1).

    IMPORTANT ordering: this context manager must be the INNER wrapper, nested
    inside _uv_stripped, so the sentinel UV layer is already in place when
    shape_key_remove(all=True) fires its depsgraph flush.
    """
    mesh = obj.data
    key = mesh.shape_keys

    if not key or not key.key_blocks:
        yield
        return

    n_verts = len(mesh.vertices)
    keep_mask = (new_index != -1)   # True for vertices that survive deletion

    # Save all key block data before stripping.
    saved_keys = []
    for kb in key.key_blocks:
        co = np.empty(n_verts * 3, dtype=np.float32)
        kb.data.foreach_get('co', co)
        saved_keys.append({
            'name':          kb.name,
            'co':            co,
            'value':         kb.value,
            'mute':          kb.mute,
            'interpolation': kb.interpolation,
            'relative_key':  kb.relative_key.name if kb.relative_key else None,
            'slider_min':    kb.slider_min,
            'slider_max':    kb.slider_max,
            'vertex_group':  kb.vertex_group,
        })

    # Remove all shape keys.  shape_key_clear() is not exposed; use the
    # operator while the object is active.
    ctx = bpy.context
    prev_active = ctx.view_layer.objects.active
    ctx.view_layer.objects.active = obj
    bpy.ops.object.shape_key_remove(all=True)
    ctx.view_layer.objects.active = prev_active

    try:
        yield
    finally:
        # After edit mode, mesh has n_new_verts vertices. Restore shape keys
        # with loose vertices remapped out.
        ctx.view_layer.objects.active = obj
        for sk in saved_keys:
            kb = obj.shape_key_add(name=sk['name'], from_mix=False)
            # Remap: keep only vertices that survived deletion.
            co_view = sk['co'].reshape(n_verts, 3)
            new_co = co_view[keep_mask].ravel()
            kb.data.foreach_set('co', new_co)
            kb.value = sk['value']
            kb.mute = sk['mute']
            kb.interpolation = sk['interpolation']
            kb.slider_min = sk['slider_min']
            kb.slider_max = sk['slider_max']
            if sk['vertex_group']:
                kb.vertex_group = sk['vertex_group']

        # Wire up relative_key references (requires all key blocks to exist first).
        if mesh.shape_keys:
            blocks = {kb.name: kb for kb in mesh.shape_keys.key_blocks}
            for sk in saved_keys:
                if sk['relative_key'] and sk['relative_key'] in blocks:
                    blocks[sk['name']].relative_key = blocks[sk['relative_key']]

        ctx.view_layer.objects.active = prev_active


def remove_loose_verts(obj):
    """Remove vertices not connected to any edge (Poser seam vertices)."""
    mesh = obj.data

    # Identify loose vertices before any stripping (needed for shape key remapping).
    loose_mask = _find_loose_mask(mesh)
    new_index, n_new_verts = _build_vertex_remap(loose_mask)

    ctx = bpy.context
    prev_active = ctx.view_layer.objects.active
    prev_selected = list(ctx.selected_objects)
    for o in prev_selected:
        o.select_set(False)
    obj.select_set(True)
    ctx.view_layer.objects.active = obj

    # bug #156097 workarounds — remove both context managers for Blender 5.2+
    # _uv_stripped is outer so the sentinel is installed before _shape_keys_stripped
    # fires shape_key_remove(all=True) and its depsgraph flush.
    with _uv_stripped(mesh):
        with _shape_keys_stripped(obj, new_index, n_new_verts):
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
