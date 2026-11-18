# SPDX-License-Identifier: GPL-3.0-or-later

import os

import bpy
from bpy.props import CollectionProperty, StringProperty

from ..core.cr2 import runtime_index
from ..core.cr2.injection_package import load_injection_package, paired_injection_path
from ..core.cr2.name_match import build_channel_index, display_name_for
from ..core.utils import _write_report
from ..properties.poserToolsPreferences import get_runtime_roots
from .applyMorphInjection import (
    PoserLibraryItem,
    _library_item_display_name,
    _save_injected_morphs_record,
    load_injected_morphs_record,
    no_morphs_report,
    say,
)

_REPORT_TEXT = "Poser Morph Removal Report"


class OT_RemoveMorphInjection_Operator(bpy.types.Operator):
    """Remove shape keys matching a Poser injection/removal package's morphs from the active mesh"""
    bl_idname = "poser.remove_morph_injection"
    bl_label = "Remove Morph Injection"
    bl_options = {'REGISTER', 'UNDO'}

    injection_filepath: StringProperty(
        name="Injection File",
        description="Either the original .pz2 that was injected, or its RemDeltas.* counterpart "
                    "-- both declare the same channel names, which is all removal needs. No "
                    "reference OBJ required: removal is pure name-matching, not geometry. "
                    "Used only when no package is picked from the list above",
        subtype='FILE_PATH',
        # Not remembered: execute() writes the picked package's path here (see Apply).
        options={'SKIP_SAVE'},
    )
    injection_library_items: CollectionProperty(type=PoserLibraryItem)
    selected_injection_name: StringProperty(
        name="Injection Package",
        description="Pick the injection or removal package from your Poser Runtime library",
        options={'SKIP_SAVE'},
    )

    @classmethod
    def poll(cls, context):
        obj = context.active_object
        return obj is not None and obj.type == 'MESH' and obj.data.shape_keys is not None

    def invoke(self, context, event):
        self.injection_library_items.clear()
        self.selected_injection_name = ""

        roots = get_runtime_roots(context)
        if roots:
            for lib_item in runtime_index.get_all_items(roots):
                if lib_item.ext not in ("pz2", "p2z"):
                    continue
                entry = self.injection_library_items.add()
                entry.name = _library_item_display_name(lib_item)
                entry.filepath = lib_item.filepath

        return context.window_manager.invoke_props_dialog(self, width=500)

    def draw(self, context):
        layout = self.layout
        layout.use_property_split = True
        layout.use_property_decorate = False

        if self.injection_library_items:
            layout.label(text="From your Poser Runtime library:")
            layout.prop_search(self, "selected_injection_name", self, "injection_library_items")
            layout.separator()
            layout.label(text="Or specify a path manually:")
        else:
            layout.label(text="No Poser Runtime folders configured -- see Preferences.", icon='INFO')

        layout.prop(self, "injection_filepath")
        if self.selected_injection_name and self.injection_filepath:
            layout.label(text="The package picked above is used; this path is ignored.", icon='INFO')

    def execute(self, context):
        obj = context.active_object

        # A pick from the list wins over the path field (the list starts empty each time, the
        # path may be left over); see Apply.
        if self.selected_injection_name:
            item = self.injection_library_items.get(self.selected_injection_name)
            if item is not None:
                self.injection_filepath = item.filepath
            elif not self.injection_filepath:
                say(self, {'ERROR'}, f"{self.selected_injection_name!r} doesn't match a package in your Poser library.")
                return {'CANCELLED'}

        if not os.path.isfile(self.injection_filepath):
            say(self, {'ERROR'}, f"Injection file not found: {self.injection_filepath!r}")
            return {'CANCELLED'}

        pkg = load_injection_package(
            self.injection_filepath, extra_roots=get_runtime_roots(context)
        )
        index = build_channel_index(pkg["figure"])
        internal_names = list(index["by_internal"].keys())

        if not internal_names:
            say(self, *no_morphs_report(pkg))
            return {'CANCELLED'}

        shapekeys = obj.data.shape_keys.key_blocks
        injected_record = load_injected_morphs_record(obj)
        removed = []
        not_found = []   # (internal_name, shown name) pairs, so a fallback can retire entries
        record_changed = False

        def remove_morph(internal_name, name):
            nonlocal record_changed
            obj.shape_key_remove(shapekeys[name])
            removed.append(name)
            if injected_record.pop(internal_name, None) is not None:
                record_changed = True

        for internal_name in internal_names:
            # A RemDeltas.* file carries no real `name` of its own (Poser
            # only needs it to match and zero the channel, not to relabel
            # it) -- prefer what this mesh's own Apply run actually named
            # it, falling back to the picked file's own name (correct when
            # Remove is pointed at the original Inj file instead).
            name = injected_record.get(internal_name) or display_name_for(index, internal_name)
            # "-" is Poser's own placeholder for "no name set" (every
            # RemDeltas.* file uses it) -- fall back to the internal name so
            # the report reads as "PBMDC_39: not present", not "-: not present".
            if name == "-":
                name = internal_name
            # A malformed/empty channel name could theoretically resolve to
            # "" or the Basis key's own name -- never a real morph, and
            # never something this operator should ever remove.
            if not name or name == "Basis":
                continue
            if name in shapekeys:
                remove_morph(internal_name, name)
            else:
                not_found.append((internal_name, name))

        # Two real RemDeltas.* files name a different channel than their
        # InjDeltas.* pair (docs/handoff-morph-injection.md, Phase 6), so their
        # channels match nothing on the mesh. Only when a Rem file matched
        # nothing at all, fall back to the channels of the Inj file with the
        # same name: Poser pairs the two by file name, and the Inj file's
        # channels are the ones Apply recorded.
        fallback_notes = []
        unmatched = {internal for internal, _name in not_found}
        for leaf in pkg["leaf_paths"] if unmatched else []:
            paired = paired_injection_path(leaf)
            if paired is None:
                continue
            leaf_channels = set(build_channel_index(load_injection_package(leaf)["figure"])["by_internal"])
            if not leaf_channels or not leaf_channels <= unmatched:
                continue
            paired_index = build_channel_index(load_injection_package(paired)["figure"])
            hits = 0
            for internal_name in paired_index["by_internal"]:
                name = injected_record.get(internal_name) or display_name_for(paired_index, internal_name)
                if name and name != "Basis" and name in shapekeys:
                    remove_morph(internal_name, name)
                    hits += 1
            if hits:
                not_found = [(i, n) for i, n in not_found if i not in leaf_channels]
                fallback_notes.append(
                    f"{os.path.basename(leaf)} names channel(s) {sorted(leaf_channels)} that "
                    f"{os.path.basename(paired)} doesn't; removed by the Inj file's channels instead."
                )

        if record_changed:
            _save_injected_morphs_record(obj, injected_record)

        header = [
            f"Injection file: {self.injection_filepath}",
            f"Removed {len(removed)}/{len(internal_names)} morph(s).",
        ]
        if pkg["unresolved_paths"]:
            header.append(
                f"{len(pkg['unresolved_paths'])} readScript reference(s) could not be resolved: "
                f"{pkg['unresolved_paths']}"
            )
        header.extend(fallback_notes)
        lines = header + ["", "Removed:", *[f"  {n}" for n in removed],
                           "", "Not present (nothing to remove):", *[f"  {n}" for _i, n in not_found]]
        _write_report(context, _REPORT_TEXT, lines)

        summary = f"Removed {len(removed)}/{len(internal_names)} morph(s) from {os.path.basename(self.injection_filepath)}"
        say(self, {'INFO'}, f"{summary} — see the '{_REPORT_TEXT}' text block.")
        return {'FINISHED'}
