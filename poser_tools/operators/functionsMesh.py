import bpy
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


def remove_unused_material_slots(context, obj):
    """Remove material slots not referenced by any face."""
    context.view_layer.objects.active = obj
    bpy.ops.object.material_slot_remove_unused()


