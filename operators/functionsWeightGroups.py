import re
import bpy


def strip_trailing_digits(text):
    matched = re.search(r'\.[0-9]{3}', text)

    if matched is None:
        return text

    return re.sub(r'\.[0-9]{3}', '', text)


def convert_to_blender_suffix(text):
    matched_left = re.search('Left_', text) or re.search(r'^l(?=[A-Z])', text)
    matched_right = re.search('Right_', text) or re.search(r'^r(?=[A-Z])', text)

    if matched_left is None and matched_right is None:
        return text  # YEET!

    if matched_left is not None:
        if re.search('Left_', text) is not None:
            return text.replace('Left_', '') + '.L'

        return text.replace(r'^l', '') + '.L'

    if matched_right is not None:
        if re.search('Right_', text) is not None:
            return text.replace('Right_', '') + '.R'

        return text.replace(r'^r', '') + '.R'


def rename_vertex_groups(mesh):
    vertex_groups = mesh.vertex_groups

    for vg in vertex_groups:
        vg_name_stripped = strip_trailing_digits(vg.name)
        vg_name_renamed = convert_to_blender_suffix(vg_name_stripped)

        vg.name = vg_name_renamed


def prefix_vertex_groups(mesh, prefix="DEF-"):
    mesh = bpy.context.active_object
    vertex_groups = mesh.vertex_groups

    for vg in vertex_groups:
        vg.name = prefix + vg.name
