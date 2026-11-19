# SPDX-License-Identifier: GPL-3.0-or-later

import os

import bpy
from bpy.props import CollectionProperty, StringProperty

from ..core.cr2 import runtime_index
from ..core.cr2.mat_pose import match_materials_to_slots, read_mat_pose
from ..core.material_builder import build_material_nodes, make_image_loader
from ..core.utils import _write_report
from ..properties.poserToolsPreferences import get_runtime_roots
from .applyMorphInjection import PoserLibraryItem, _library_item_display_name, say

_REPORT_TEXT = "Poser MAT Pose Report"
# On a slot material: the name of the copy kept from before the first MAT replaced its nodes.
_BACKUP_PROP = "poser_mat_backup"

# The dialog's preview reads the picked file on every redraw; a big prop MAT is 15,000 lines.
_preview_cache = {}


def _read_cached(path):
    """{name: PoserMaterial} for `path`, re-read only when the file changes; None if unreadable."""
    try:
        stamp = os.stat(path).st_mtime_ns
    except OSError:
        return None
    cached = _preview_cache.get(path)
    if cached is None or cached[0] != stamp:
        try:
            cached = (stamp, read_mat_pose(path))
        except (OSError, ValueError) as exc:
            cached = (stamp, exc)
        _preview_cache.clear()          # one file at a time is all the dialog needs
        _preview_cache[path] = cached
    return None if isinstance(cached[1], Exception) else cached[1]


def _slot_material_names(obj):
    return [slot.material.name for slot in obj.material_slots if slot.material]


class OT_ApplyMatPose_Operator(bpy.types.Operator):
    """Apply a Poser MAT pose (.pz2) to the active mesh's materials, keeping the originals"""
    bl_idname = "poser.apply_mat_pose"
    bl_label = "Apply MAT Pose"
    bl_options = {'REGISTER', 'UNDO'}

    mat_filepath: StringProperty(
        name="MAT File",
        description="A Poser MAT pose (.pz2): material settings only. Used only when nothing is "
                    "picked from the list above",
        subtype='FILE_PATH',
        options={'SKIP_SAVE'},
    )
    library_items: CollectionProperty(type=PoserLibraryItem)
    selected_mat_name: StringProperty(
        name="MAT Pose",
        description="Pick the MAT pose from your Poser Runtime library",
        options={'SKIP_SAVE'},
    )

    @classmethod
    def poll(cls, context):
        obj = context.active_object
        if obj is None or obj.type != 'MESH' or not _slot_material_names(obj):
            cls.poll_message_set("Select a mesh with materials")
            return False
        return True

    def invoke(self, context, event):
        given = os.path.normcase(self.mat_filepath) if self.mat_filepath else ""
        self.library_items.clear()
        self.selected_mat_name = ""
        roots = get_runtime_roots(context)
        if roots:
            for item in runtime_index.get_all_items(roots):
                if item.ext not in ("pz2", "p2z"):
                    continue
                entry = self.library_items.add()
                entry.name = _library_item_display_name(item)
                entry.filepath = item.filepath
                if given and os.path.normcase(item.filepath) == given:
                    self.selected_mat_name = entry.name
        if self.selected_mat_name:
            self.mat_filepath = ""
        # The dialog is the confirmation (owner's call): it says what will be replaced before OK.
        return context.window_manager.invoke_props_dialog(self, width=560, confirm_text="Apply")

    def _chosen_path(self):
        # A pick from the list wins over the path field, as in Apply Morph Injection.
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
        path = self._chosen_path()
        box = layout.box()
        if not path or not os.path.isfile(path):
            box.label(text="Pick a MAT pose to see what it will change.", icon='INFO')
            return
        materials = _read_cached(path)
        if not materials:
            box.label(text="This file has no materials: not a MAT pose.", icon='ERROR')
            return
        match = match_materials_to_slots(list(materials), _slot_material_names(obj), obj.name)
        box.label(text=f"Replaces {len(match['pairs'])} of {obj.name}'s "
                       f"{len(_slot_material_names(obj))} materials.", icon='MATERIAL')
        if match["no_slot"]:
            shown = ", ".join(match["no_slot"][:4]) + (" ..." if len(match["no_slot"]) > 4 else "")
            box.label(text=f"{len(match['no_slot'])} in the file have no slot on this mesh: {shown}")
        box.label(text="Each original is kept as '<name> (original)' the first time a MAT replaces it.")

    def execute(self, context):
        obj = context.active_object
        path = self._chosen_path()
        if not path or not os.path.isfile(path):
            say(self, {'ERROR'}, f"MAT file not found: {path!r}")
            return {'CANCELLED'}
        try:
            materials = read_mat_pose(path)
        except (OSError, ValueError) as exc:
            say(self, {'ERROR'}, f"Couldn't read {os.path.basename(path)}: {exc}")
            return {'CANCELLED'}
        name = os.path.basename(path)
        if not materials:
            say(self, {'WARNING'}, f"{name} has no materials: not a MAT pose.")
            return {'CANCELLED'}
        match = match_materials_to_slots(list(materials), _slot_material_names(obj), obj.name)
        if not match["pairs"]:
            say(self, {'WARNING'}, f"None of {name}'s {len(materials)} materials match a material on "
                                   f"{obj.name}. See the material names in its slots.")
            return {'CANCELLED'}

        warnings = []
        load_image = make_image_loader(path, get_runtime_roots(context), lambda _t, msg: warnings.append(msg))
        replaced, backups = [], []
        for mat_name, slot_name in match["pairs"]:
            bl_mat = bpy.data.materials[slot_name]
            if _BACKUP_PROP not in bl_mat:
                # Only the first time: a second MAT must not overwrite the import's original.
                backup = bl_mat.copy()
                backup.name = f"{slot_name} (original)"
                backup.use_fake_user = True          # kept in the file with nothing using it
                bl_mat[_BACKUP_PROP] = backup.name
                backups.append(backup.name)
            kind = build_material_nodes(bl_mat, materials[mat_name], load_image,
                                        reporter=lambda _t, msg, m=slot_name: warnings.append(f"{m}: {msg}"))
            replaced.append(f"  {slot_name} <- {mat_name} ({kind})")

        lines = [f"MAT file: {path}", f"Mesh: {obj.name}",
                 f"Replaced {len(replaced)} material(s).",
                 f"Originals kept (first MAT on these materials): {len(backups)}"]
        if match["no_slot"]:
            lines.append(f"{len(match['no_slot'])} material(s) in the file have no slot on this mesh: {match['no_slot']}")
        if match["untouched"]:
            lines.append(f"{len(match['untouched'])} of the mesh's materials aren't in the file, left as they were: "
                         f"{match['untouched']}")
        if warnings:
            lines += ["", f"{len(warnings)} warning(s):", *[f"  {w}" for w in warnings]]
        lines += ["", "Replaced:", *replaced]
        if backups:
            lines += ["", "Originals kept as:", *[f"  {b}" for b in backups]]
        _write_report(context, _REPORT_TEXT, lines)

        say(self, {'WARNING'} if warnings else {'INFO'},
            f"Applied {len(replaced)} material(s) from {name} to {obj.name}"
            f"{f', {len(warnings)} warning(s)' if warnings else ''} — see the '{_REPORT_TEXT}' text block.")
        return {'FINISHED'}
