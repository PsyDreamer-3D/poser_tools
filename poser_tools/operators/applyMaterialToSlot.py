# SPDX-License-Identifier: GPL-3.0-or-later
"""Apply a Poser single material (.mt5/.mz5) to the active material slot (Phase 8.4).

In Poser a single material is applied to whichever material is selected; the name inside the file
(nearly always `Preview`, under `actor $CURRENT`) means nothing, so there is no matching to do. The
rest is the MAT pose machinery: the same reader, builder, texture lookup and first-time backup.
"""

import os

import bpy
from bpy.props import CollectionProperty, StringProperty

from ..core.cr2.mat_pose import read_mat_pose
from ..core.material_builder import build_material_nodes, make_image_loader
from ..core.utils import _write_report
from ..properties.poserToolsPreferences import get_runtime_roots
from .applyMatPose import _REPORT_TEXT, _read_cached, keep_original, library_items
from .applyMorphInjection import PoserLibraryItem, say


def _active_slot_material(obj):
    if obj is None or obj.type != 'MESH' or not obj.material_slots:
        return None
    return obj.material_slots[obj.active_material_index].material


class OT_ApplyMaterialToSlot_Operator(bpy.types.Operator):
    """Apply a Poser single material (.mt5) to the active material slot, keeping the original"""
    bl_idname = "poser.apply_material_to_slot"
    bl_label = "Apply Material to Slot"
    bl_options = {'REGISTER', 'UNDO'}

    mat_filepath: StringProperty(
        name="Material File",
        description="A Poser single material (.mt5/.mz5). Used only when nothing is picked from "
                    "the list above",
        subtype='FILE_PATH',
        options={'SKIP_SAVE'},
    )
    library_items: CollectionProperty(type=PoserLibraryItem)
    selected_mat_name: StringProperty(
        name="Material",
        description="Pick a single material from your Poser Runtime library",
        options={'SKIP_SAVE'},
    )

    @classmethod
    def poll(cls, context):
        if _active_slot_material(context.active_object) is None:
            cls.poll_message_set("Select a mesh and a material slot that has a material")
            return False
        return True

    def invoke(self, context, event):
        self.selected_mat_name = library_items(context, self.library_items, ("mt5", "mz5"), self.mat_filepath)
        if self.selected_mat_name:
            self.mat_filepath = ""
        return context.window_manager.invoke_props_dialog(self, width=560, confirm_text="Apply")

    def _chosen_path(self):
        item = self.library_items.get(self.selected_mat_name) if self.selected_mat_name else None
        return item.filepath if item is not None else self.mat_filepath

    def draw(self, context):
        layout = self.layout
        layout.use_property_split = True
        layout.use_property_decorate = False
        if self.library_items:
            layout.prop_search(self, "selected_mat_name", self, "library_items")
        layout.prop(self, "mat_filepath")

        obj = context.active_object
        target = _active_slot_material(obj)
        box = layout.box()
        path = self._chosen_path()
        if not path or not os.path.isfile(path):
            box.label(text=f"Pick a material to apply to {target.name}.", icon='INFO')
            return
        materials = _read_cached(path)
        if not materials:
            box.label(text="This file has no materials.", icon='ERROR')
            return
        box.label(text=f"Replaces {target.name} (slot {obj.active_material_index + 1} of {obj.name}).",
                  icon='MATERIAL')
        if len(materials) > 1:
            box.label(text=f"The file holds {len(materials)} materials; the first, {next(iter(materials))}, is used.")
        box.label(text="The original is kept as '<name> (original)' the first time it is replaced.")

    def execute(self, context):
        obj = context.active_object
        bl_mat = _active_slot_material(obj)
        path = self._chosen_path()
        if not path or not os.path.isfile(path):
            say(self, {'ERROR'}, f"Material file not found: {path!r}")
            return {'CANCELLED'}
        name = os.path.basename(path)
        try:
            materials = read_mat_pose(path)
        except (OSError, ValueError) as exc:
            say(self, {'ERROR'}, f"Couldn't read {name}: {exc}")
            return {'CANCELLED'}
        if not materials:
            say(self, {'WARNING'}, f"{name} has no materials.")
            return {'CANCELLED'}
        source_name, pmat = next(iter(materials.items()))

        warnings, texture_notes = [], []

        def collect(types, message):
            (warnings if 'WARNING' in types else texture_notes).append(message)

        load_image = make_image_loader(path, get_runtime_roots(context), collect)
        target_name = bl_mat.name
        backup = keep_original(bl_mat)
        kind = build_material_nodes(bl_mat, pmat, load_image, reporter=lambda t, msg: collect(t, msg))

        lines = [f"Material file: {path}", f"Mesh: {obj.name}",
                 f"Replaced {target_name} (slot {obj.active_material_index + 1}) <- {source_name} ({kind})",
                 f"Original kept as: {backup}" if backup else "Original already kept from an earlier replacement."]
        if len(materials) > 1:
            lines.append(f"The file holds {len(materials)} materials; only the first was used: {list(materials)}")
        if warnings:
            lines += ["", f"{len(warnings)} warning(s):", *[f"  {w}" for w in warnings]]
        if texture_notes:
            lines += ["", f"{len(texture_notes)} texture(s) found the way Poser looks for them, not at the path written:",
                      *[f"  {n}" for n in texture_notes]]
        _write_report(context, _REPORT_TEXT, lines)

        say(self, {'WARNING'} if warnings else {'INFO'},
            f"Applied {name} to {target_name}"
            f"{f', {len(warnings)} warning(s)' if warnings else ''} — see the '{_REPORT_TEXT}' text block.")
        return {'FINISHED'}
