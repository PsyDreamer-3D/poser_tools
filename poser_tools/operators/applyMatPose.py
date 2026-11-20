# SPDX-License-Identifier: GPL-3.0-or-later

import os

import bpy
from bpy.props import CollectionProperty, StringProperty

from ..core.cr2 import runtime_index
from ..core.cr2.mat_pose import match_materials_to_slots, read_mat_pose, slot_prefix
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


def keep_original(bl_mat):
    """Keep a fake-user copy of `bl_mat` the first time Poser materials replace it; its name, or None.

    Only the first time: a second MAT, collection or material must not overwrite the import's
    original (owner's call, Phase 8.1).
    """
    if _BACKUP_PROP in bl_mat:
        return None
    original_name = bl_mat.name
    backup = bl_mat.copy()
    backup.name = f"{original_name} (original)"
    backup.use_fake_user = True          # kept in the file with nothing using it
    bl_mat[_BACKUP_PROP] = backup.name
    return backup.name


def library_items(context, collection, extensions, given_path=""):
    """Fill `collection` with the Runtime library's files of `extensions`; the display name of
    `given_path` if it is among them, else ""."""
    given = os.path.normcase(given_path) if given_path else ""
    picked = ""
    collection.clear()
    roots = get_runtime_roots(context)
    if roots:
        for item in runtime_index.get_all_items(roots):
            if item.ext not in extensions:
                continue
            entry = collection.add()
            entry.name = _library_item_display_name(item)
            entry.filepath = item.filepath
            if given and os.path.normcase(item.filepath) == given:
                picked = entry.name
    return picked


def _slot_material_names(obj):
    return [slot.material.name for slot in obj.material_slots if slot.material]


def _draw_name_list(layout, heading, names, per_line=5, max_lines=3):
    """`heading` and the names under it, a few per line, capped with "and N more"."""
    layout.label(text=heading)
    shown = names[:per_line * max_lines]
    for start in range(0, len(shown), per_line):
        layout.label(text="    " + ", ".join(shown[start:start + per_line]))
    if len(names) > len(shown):
        layout.label(text=f"    and {len(names) - len(shown)} more (all in the report after Apply)")


class OT_ApplyMatPose_Operator(bpy.types.Operator):
    """Apply a Poser MAT pose (.pz2) or material collection (.mc6) to the active mesh's materials, keeping the originals"""
    bl_idname = "poser.apply_mat_pose"
    bl_label = "Apply MAT Pose or Collection"
    bl_options = {'REGISTER', 'UNDO'}

    mat_filepath: StringProperty(
        name="MAT File",
        description="A Poser MAT pose (.pz2/.p2z) or material collection (.mc6/.mcz): materials "
                    "applied by name. Used only when nothing is picked from the list above",
        subtype='FILE_PATH',
        options={'SKIP_SAVE'},
    )
    library_items: CollectionProperty(type=PoserLibraryItem)
    selected_mat_name: StringProperty(
        name="MAT Pose or Collection",
        description="Pick a MAT pose or material collection from your Poser Runtime library",
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
        # A collection is applied the way a MAT pose is (by name, to the selected object), so it
        # shares this dialog (Phase 8.4).
        self.selected_mat_name = library_items(context, self.library_items, ("pz2", "p2z", "mc6", "mcz"),
                                               self.mat_filepath)
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
            box.label(text="Pick a MAT pose or material collection to see what it will change.", icon='INFO')
            return
        materials = _read_cached(path)
        if not materials:
            box.label(text="This file has no materials.", icon='ERROR')
            return
        slots = _slot_material_names(obj)
        match = match_materials_to_slots(list(materials), slots, obj.name)
        box.label(text=f"Replaces {len(match['pairs'])} of {obj.name}'s {len(slots)} materials.", icon='MATERIAL')
        # Both sides of a mismatch, before anything changes: a run that matched 26 of 34 once
        # (owner's report, Phase 8) could only be explained from the report afterwards.
        if match["no_slot"]:
            _draw_name_list(box, f"{len(match['no_slot'])} in the file have no material of that name on this mesh:",
                            match["no_slot"])
        if match["untouched"]:
            prefix = slot_prefix(slots, obj.name)
            bare = [name[len(prefix):] if name.startswith(prefix) else name for name in match["untouched"]]
            _draw_name_list(box, f"{len(bare)} of the mesh's materials aren't in the file and stay as they are:", bare)
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
            say(self, {'WARNING'}, f"{name} has no materials.")
            return {'CANCELLED'}
        match = match_materials_to_slots(list(materials), _slot_material_names(obj), obj.name)
        if not match["pairs"]:
            say(self, {'WARNING'}, f"None of {name}'s {len(materials)} materials match a material on "
                                   f"{obj.name}. See the material names in its slots.")
            return {'CANCELLED'}

        warnings, texture_notes = [], []

        def collect(types, message):
            (warnings if 'WARNING' in types else texture_notes).append(message)

        load_image = make_image_loader(path, get_runtime_roots(context), collect)
        replaced, backups = [], []
        for mat_name, slot_name in match["pairs"]:
            bl_mat = bpy.data.materials[slot_name]
            backup = keep_original(bl_mat)
            if backup:
                backups.append(backup)
            kind = build_material_nodes(bl_mat, materials[mat_name], load_image,
                                        reporter=lambda t, msg, m=slot_name: collect(t, f"{m}: {msg}"))
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
        if texture_notes:
            lines += ["", f"{len(texture_notes)} texture(s) found the way Poser looks for them, not at the path written:",
                      *[f"  {n}" for n in texture_notes]]
        lines += ["", "Replaced:", *replaced]
        if backups:
            lines += ["", "Originals kept as:", *[f"  {b}" for b in backups]]
        _write_report(context, _REPORT_TEXT, lines)

        say(self, {'WARNING'} if warnings else {'INFO'},
            f"Applied {len(replaced)} material(s) from {name} to {obj.name}"
            f"{f', {len(warnings)} warning(s)' if warnings else ''} — see the '{_REPORT_TEXT}' text block.")
        return {'FINISHED'}
