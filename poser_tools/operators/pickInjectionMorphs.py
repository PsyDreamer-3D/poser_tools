# SPDX-License-Identifier: GPL-3.0-or-later

import json
import os

import bpy
from bpy.props import BoolProperty, CollectionProperty, IntProperty, StringProperty

from ..core.cr2.injection_scan import classify_morphs, estimate_apply, scan_injection_package
from ..core.functionsShapeKeys import is_jcm_shapekey
from ..properties.poserToolsPreferences import get_runtime_roots
from .applyMorphInjection import _CORRESPONDENCE_PROP, call_nested, no_morphs_report, say


class PoserMorphRow(bpy.types.PropertyGroup):
    """One morph of the package being picked from."""
    name: StringProperty()                 # the label Apply will give the shape key
    internal_name: StringProperty()
    status: StringProperty()               # why it can't be chosen; "" when it can
    leaves: StringProperty()               # the files to parse for it, newline-separated
    selected: BoolProperty(default=False)
    locked: BoolProperty(default=False)
    delta_count: IntProperty()             # for the estimate


def _select_all_update(self, context):
    if self.select_all:
        for row in self.rows:
            if not row.locked:
                row.selected = True
        # A toggle that behaves like a button: pressing it twice should work twice.
        self["select_all"] = False


def _select_none_update(self, context):
    if self.select_none:
        for row in self.rows:
            row.selected = False
        self["select_none"] = False


def _format_size(n_bytes):
    if n_bytes >= 1024 ** 3:
        return f"{n_bytes / 1024 ** 3:.1f} GB"
    return f"{n_bytes / 1024 ** 2:.0f} MB"


class OT_PickInjectionMorphs_Operator(bpy.types.Operator):
    """Choose which morphs of an injection package to apply to the active mesh"""
    bl_idname = "poser.pick_injection_morphs"
    bl_label = "Choose Morphs to Apply"
    # Needs its paths filled in by the caller (Apply's popup, later the asset shelf), so it
    # isn't offered in search. It changes nothing itself.
    bl_options = {'INTERNAL'}

    injection_filepath: StringProperty(subtype='FILE_PATH')
    reference_obj_filepath: StringProperty(subtype='FILE_PATH')
    rows: CollectionProperty(type=PoserMorphRow)
    active_row: IntProperty()
    select_all: BoolProperty(name="Select All", default=False, update=_select_all_update)
    select_none: BoolProperty(name="Select None", default=False, update=_select_none_update)
    n_vertices: IntProperty(options={'HIDDEN', 'SKIP_SAVE'})
    correspondence_cached: BoolProperty(options={'HIDDEN', 'SKIP_SAVE'})

    @classmethod
    def poll(cls, context):
        return context.active_object is not None and context.active_object.type == 'MESH'

    def invoke(self, context, event):
        obj = context.active_object
        scan = scan_injection_package(self.injection_filepath, extra_roots=get_runtime_roots(context))
        if not scan["morphs"]:
            say(self, *no_morphs_report(scan))
            return {'CANCELLED'}

        existing = obj.data.shape_keys.key_blocks.keys() if obj.data.shape_keys else []
        classified = classify_morphs(scan["morphs"], existing, is_jcm_shapekey)
        choosable = [morph for morph, status in classified if not status]

        if not choosable:
            reasons = sorted({status for _m, status in classified})
            say(self,
                {'WARNING'},
                f"Nothing to apply from {os.path.basename(self.injection_filepath)}: "
                f"all {len(classified)} morph(s) are {', '.join(reasons)}.",
            )
            return {'CANCELLED'}
        if len(classified) == 1:
            # A package of one morph has nothing to choose between. Judged on the package, not
            # on what is choosable: a long package with one new morph still shows its list, so
            # the greyed rows say why the rest were left out instead of applying silently.
            return self._apply([(m.internal_name, m.leaf_paths) for m in choosable])

        self.rows.clear()
        for morph, status in classified:
            row = self.rows.add()
            row.name = morph.display_name
            row.internal_name = morph.internal_name
            row.status = status
            row.locked = bool(status)
            row.selected = not status
            row.leaves = "\n".join(morph.leaf_paths)
            row.delta_count = morph.delta_count
        self.n_vertices = len(obj.data.vertices)
        self.correspondence_cached = _CORRESPONDENCE_PROP in obj.data
        return context.window_manager.invoke_props_dialog(self, width=520)

    def draw(self, context):
        layout = self.layout
        picked = [r for r in self.rows if r.selected and not r.locked]
        choosable = sum(1 for r in self.rows if not r.locked)

        layout.label(text=os.path.basename(self.injection_filepath), icon='SHAPEKEY_DATA')
        layout.template_list("POSER_UL_injection_morphs", "", self, "rows", self, "active_row", rows=10)

        row = layout.row(align=True)
        row.prop(self, "select_all", toggle=True)
        row.prop(self, "select_none", toggle=True)

        box = layout.box()
        box.label(text=f"Apply {len(picked)} of {choosable} morphs")
        seconds, n_bytes = estimate_apply(picked, self.n_vertices)
        box.label(text=f"About {seconds:.0f} s to read and add, {_format_size(n_bytes)} of shape-key data")
        box.label(text="Vertex matching: reused from this mesh's cache if it is unchanged"
                  if self.correspondence_cached else "Vertex matching: about 30 s the first time on this mesh")

    def execute(self, context):
        picked = [r for r in self.rows if r.selected and not r.locked]
        if not picked:
            say(self, {'WARNING'}, "No morphs chosen -- nothing applied.")
            return {'CANCELLED'}
        return self._apply([(r.internal_name, r.leaves.split("\n")) for r in picked])

    def _apply(self, picked):
        """Hand the choice, a list of (internal_name, leaf paths), to Apply Morph Injection."""
        leaves = []
        for _internal_name, paths in picked:
            leaves.extend(p for p in paths if p not in leaves)
        call_nested(
            self, bpy.ops.poser.apply_morph_injection,
            injection_filepath=self.injection_filepath,
            reference_obj_filepath=self.reference_obj_filepath,
            selected_morphs=json.dumps([internal_name for internal_name, _paths in picked]),
            selected_leaves=json.dumps(leaves),
        )
        return {'FINISHED'}

