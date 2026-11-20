# SPDX-License-Identifier: GPL-3.0-or-later
"""Apply a Poser single material (.mt5/.mz5) to one of the mesh's materials (Phase 8.4).

In Poser a single material is applied to whichever part or material is selected; the name inside the
file (nearly always `Preview`, under `actor $CURRENT`) means nothing, so there is no matching to do.
Blender has no "selected part" to stand in for that, so the dialog asks which of the mesh's materials
to replace, starting from the active slot's (owner, 2026-11-19). The rest is the MAT pose machinery:
the same reader, builder, texture lookup and first-time backup.
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


def _mesh_materials(obj):
    """The materials in the mesh's slots, in slot order, each once."""
    if obj is None or obj.type != 'MESH':
        return []
    return list(dict.fromkeys(slot.material for slot in obj.material_slots if slot.material))


def _active_slot_material(obj):
    if obj is None or obj.type != 'MESH' or not obj.material_slots:
        return None
    return obj.material_slots[obj.active_material_index].material


class OT_ApplyMaterialToSlot_Operator(bpy.types.Operator):
    """Apply a Poser single material (.mt5) to one of the active mesh's materials, keeping the original"""
    bl_idname = "poser.apply_material_to_slot"
    bl_label = "Apply Single Material"
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

    # The mesh's materials, for the searchable Material field (filled by invoke()).
    target_items: CollectionProperty(type=PoserLibraryItem)
    target_material: StringProperty(
        name="Replace",
        description="The material on this mesh to replace, as you'd select the part in Poser. "
                    "Starts as the active slot's material",
        options={'SKIP_SAVE'},
    )

    @classmethod
    def poll(cls, context):
        if not _mesh_materials(context.active_object):
            cls.poll_message_set("Select a mesh with materials")
            return False
        return True

    def invoke(self, context, event):
        self.selected_mat_name = library_items(context, self.library_items, ("mt5", "mz5"), self.mat_filepath)
        if self.selected_mat_name:
            self.mat_filepath = ""
        obj = context.active_object
        self.target_items.clear()
        for material in _mesh_materials(obj):
            self.target_items.add().name = material.name
        if not self.target_material:
            active = _active_slot_material(obj)
            self.target_material = active.name if active else self.target_items[0].name
        return context.window_manager.invoke_props_dialog(self, width=560, confirm_text="Apply")

    def _target(self, obj):
        """The material to replace: the one picked, else (a scripted call) the active slot's.

        Only a material on this mesh: a name typed or left over from elsewhere is refused.
        """
        if not self.target_material:
            return _active_slot_material(obj)
        return next((m for m in _mesh_materials(obj) if m.name == self.target_material), None)

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
        layout.separator()
        layout.prop_search(self, "target_material", self, "target_items", icon='MATERIAL')
        target = self._target(obj)
        box = layout.box()
        if target is None:
            box.label(text=f"Pick one of {obj.name}'s materials to replace.", icon='ERROR')
            return
        path = self._chosen_path()
        if not path or not os.path.isfile(path):
            box.label(text=f"Pick a material file to apply to {target.name}.", icon='INFO')
            return
        materials = _read_cached(path)
        if not materials:
            box.label(text="This file has no materials.", icon='ERROR')
            return
        box.label(text=f"Replaces {target.name} on {obj.name}.", icon='MATERIAL')
        if len(materials) > 1:
            box.label(text=f"The file holds {len(materials)} materials; the first, {next(iter(materials))}, is used.")
        box.label(text="The original is kept as '<name> (original)' the first time it is replaced.")

    def execute(self, context):
        obj = context.active_object
        bl_mat = self._target(obj)
        if bl_mat is None:
            say(self, {'ERROR'}, f"{self.target_material!r} isn't one of {obj.name}'s materials.")
            return {'CANCELLED'}
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
                 f"Replaced {target_name} <- {source_name} ({kind})",
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
