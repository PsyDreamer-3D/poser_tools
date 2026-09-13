# SPDX-License-Identifier: GPL-3.0-or-later

import os
import threading
import time

import bpy
import numpy as np
from bpy.props import StringProperty

from ..core.cr2.actor_vertex_index import load_obj_actor_vertex_groups
from ..core.cr2.apply_injection import build_shape_key_positions
from ..core.cr2.injection_package import load_injection_package
from ..core.cr2.mesh_correspondence import build_vertex_correspondence
from ..core.cr2.name_match import build_channel_index, match_channel_group
from ..core.cr2.obj_io import load_obj_vertex_positions
from ..core.functionsShapeKeys import is_jcm_shapekey
from ..core.utils import _write_report

_REPORT_TEXT = "Poser Morph Injection Report"
_REFERENCE_OBJ_PROP = "poser_reference_obj"


class OT_ApplyMorphInjection_Operator(bpy.types.Operator):
    """Apply every morph in a 3rd-party Poser injection package (.pz2) to the active mesh as new shape keys"""
    bl_idname = "poser.apply_morph_injection"
    bl_label = "Apply Morph Injection"
    bl_options = {'REGISTER', 'UNDO'}

    injection_filepath: StringProperty(
        name="Injection File",
        description="A Poser morph injection .pz2 (a single inline-delta file, or an "
                    "orchestrator that readScript-references several)",
        subtype='FILE_PATH',
    )
    reference_obj_filepath: StringProperty(
        name="Reference OBJ",
        description="The figure's own reference geometry (the file the CR2's figureResFile "
                    "names) -- needed to translate the injection's Poser-native vertex indices "
                    "onto this mesh",
        subtype='FILE_PATH',
    )

    @classmethod
    def poll(cls, context):
        return context.active_object is not None and context.active_object.type == 'MESH'

    def invoke(self, context, event):
        # Remembered from a prior run on this mesh, if any -- re-applying a
        # second package to the same figure shouldn't have to re-browse for
        # its own reference OBJ every time.
        self.reference_obj_filepath = context.active_object.get(_REFERENCE_OBJ_PROP, "")
        return context.window_manager.invoke_props_dialog(self, width=500)

    def draw(self, context):
        layout = self.layout
        layout.use_property_split = True
        layout.use_property_decorate = False
        layout.prop(self, "injection_filepath")
        layout.prop(self, "reference_obj_filepath")

    def execute(self, context):
        self._op_start_time = time.time()
        obj = context.active_object

        if not os.path.isfile(self.injection_filepath):
            self.report({'ERROR'}, f"Injection file not found: {self.injection_filepath!r}")
            return {'CANCELLED'}
        if not os.path.isfile(self.reference_obj_filepath):
            self.report({'ERROR'}, f"Reference OBJ not found: {self.reference_obj_filepath!r}")
            return {'CANCELLED'}

        # Remember the reference OBJ for next time even if the rest of this
        # run fails partway through -- it was still a correct answer.
        obj[_REFERENCE_OBJ_PROP] = self.reference_obj_filepath

        pkg = load_injection_package(self.injection_filepath)
        index = build_channel_index(pkg["figure"])
        morph_names = list(index["by_internal"].keys())

        if not morph_names:
            self.report({'WARNING'}, "No targetGeom morphs found in this injection package.")
            return {'CANCELLED'}

        obj_verts = load_obj_vertex_positions(self.reference_obj_filepath)
        actor_obj_groups = load_obj_actor_vertex_groups(self.reference_obj_filepath)

        if obj.data.shape_keys is None:
            obj.shape_key_add(name='Basis', from_mix=False)
        basis_key = obj.data.shape_keys.key_blocks['Basis']
        basis_positions = np.empty((len(basis_key.data), 3), dtype=np.float64)
        basis_key.data.foreach_get('co', basis_positions.ravel())

        self._obj = obj
        self._pkg = pkg
        self._index = index
        self._morph_names = morph_names
        self._actor_obj_groups = actor_obj_groups
        self._basis_positions = basis_positions
        self._correspondence = None
        self._thread_error = None
        # Written by build_vertex_correspondence's progress_callback -- from
        # the background thread in the interactive path below, so this stays
        # a plain attribute write (no bpy call), and modal()'s timer tick
        # (main thread) is what turns it into a real wm.progress_update().
        self._correspondence_progress = 0.0

        wm = context.window_manager
        wm.progress_begin(0, 100)
        wm.progress_update(5)

        if bpy.app.background:
            # Headless (`blender --background`, or a scripted bpy.ops call like
            # this repo's own e2e verification) -- no event loop ever dispatches
            # TIMER events to a modal operator here, so going modal would just
            # hang the script forever. `context.window` is *not* a reliable
            # headless check on its own -- confirmed against this Blender build,
            # a Window datablock exists even under `--background
            # --factory-startup`. `bpy.app.background` is the documented flag
            # for exactly this. Run the slow step inline like before instead.
            try:
                self._correspondence = build_vertex_correspondence(
                    obj_verts, basis_positions, progress_callback=self._set_correspondence_progress
                )
                return self._apply_morphs(context)
            finally:
                wm.progress_end()

        # Interactive: build_vertex_correspondence is the one genuinely slow step
        # -- originally ~1-5 min on a 72k-vertex figure, now ~30s after the
        # Phase 5.2 rewrite (docs/handoff-morph-injection.md), but running any
        # multi-second call inline here still freezes Blender's UI with no
        # repaint and no way to tell it apart from a real hang, as real UAT
        # confirmed even before that rewrite. NumPy's C-level ops release the
        # GIL, and CPython's own bytecode-level GIL switching keeps this
        # thread from starving the main thread regardless -- so a background
        # thread plus a modal timer keeps Blender responsive without
        # threading a progress callback through mesh_correspondence.py itself.
        def _worker():
            try:
                self._correspondence = build_vertex_correspondence(
                    obj_verts, basis_positions, progress_callback=self._set_correspondence_progress
                )
            except Exception as exc:
                self._thread_error = exc

        self._start_time = time.time()
        self._thread = threading.Thread(target=_worker, daemon=True)
        self._thread.start()
        self._timer = wm.event_timer_add(0.15, window=context.window)
        context.workspace.status_text_set(
            "Poser Morph Injection: building vertex correspondence... 0% 0s (Esc to cancel)"
        )
        # A status-bar text alone turned out to be easy to miss entirely
        # (confirmed via UAT: "no signs that anything was happening") --
        # the wait cursor is the one signal every desktop user already
        # recognizes as "the app is busy," regardless of whether they're
        # looking at the status bar.
        context.window.cursor_set('WAIT')
        wm.modal_handler_add(self)
        return {'RUNNING_MODAL'}

    def _set_correspondence_progress(self, fraction):
        self._correspondence_progress = fraction

    def modal(self, context, event):
        if event.type == 'ESC':
            self._finish_modal(context)
            context.window_manager.progress_end()
            # The thread itself isn't forcibly killed (Python threads can't be) --
            # it keeps running to completion in the background and its result is
            # simply never picked up. Safe: it only touches obj_verts/basis_positions
            # NumPy arrays, never bpy.data.
            self.report(
                {'WARNING'},
                "Apply Morph Injection cancelled -- no shape keys were added.",
            )
            return {'CANCELLED'}

        if event.type != 'TIMER':
            return {'PASS_THROUGH'}

        elapsed = time.time() - self._start_time
        # 5-90% of the overall bar is the correspondence build; 90-100% is
        # the (much faster) per-morph loop in _apply_morphs below. A real
        # percentage -- not a static number -- is what the owner asked for
        # after the status-bar text alone still read as "nothing happening."
        pct = 5 + int(85 * self._correspondence_progress)
        context.window_manager.progress_update(pct)
        context.workspace.status_text_set(
            f"Poser Morph Injection: building vertex correspondence... {pct}% {elapsed:.0f}s (Esc to cancel)"
        )

        if self._thread.is_alive():
            return {'RUNNING_MODAL'}

        self._finish_modal(context)

        if self._thread_error is not None:
            context.window_manager.progress_end()
            self.report({'ERROR'}, f"Vertex correspondence build failed: {self._thread_error}")
            return {'CANCELLED'}

        try:
            return self._apply_morphs(context)
        finally:
            context.window_manager.progress_end()

    def _finish_modal(self, context):
        context.window_manager.event_timer_remove(self._timer)
        context.workspace.status_text_set(None)
        context.window.cursor_set('DEFAULT')

    def _apply_morphs(self, context):
        obj = self._obj
        index = self._index
        morph_names = self._morph_names
        actor_obj_groups = self._actor_obj_groups
        basis_positions = self._basis_positions
        correspondence = self._correspondence
        pkg = self._pkg
        n_morphs = len(morph_names)

        wm = context.window_manager
        report_lines = []
        applied = 0
        jcm_skipped = []
        already_present = []
        empty_skipped = []

        for i, name in enumerate(morph_names):
            # 5-90% is the correspondence build (see modal()); this loop is
            # the remaining 90-100%.
            wm.progress_update(90 + int(10 * i / n_morphs))

            if is_jcm_shapekey(name):
                jcm_skipped.append(name)
                continue
            if name in obj.data.shape_keys.key_blocks:
                already_present.append(name)
                continue

            group = match_channel_group(index, name)
            result = build_shape_key_positions(
                group, actor_obj_groups, basis_positions, correspondence
            )

            if result["touched_count"] == 0:
                empty_skipped.append(name)
                report_lines.append(
                    f"{name}: skipped, nothing to apply "
                    f"(pmd_deltas_skipped={result['pmd_deltas_skipped']}, "
                    f"unmapped={result['unmapped_count']})"
                )
                continue

            sk = obj.shape_key_add(name=name, from_mix=False)
            sk.data.foreach_set('co', result["positions"].ravel())
            # shape_key_add() defaults a new key's value to 1.0 (fully dialed
            # in) -- fine for one morph, but a package applying dozens at
            # once would otherwise stack all of them at full strength
            # simultaneously. Rest at 0, like a Poser/DAZ dial, and let the
            # user dial each one in deliberately.
            sk.value = 0.0
            applied += 1
            report_lines.append(
                f"{name}: touched={result['touched_count']} "
                f"unmapped={result['unmapped_count']} "
                f"pmd_deltas_skipped={result['pmd_deltas_skipped']} "
                f"collisions_resolved={result['collisions_resolved']} "
                f"seam_collisions_skipped={result['seam_collisions_skipped']}"
            )

        total_elapsed = time.time() - self._op_start_time
        header = [f"Injection file: {self.injection_filepath}",
                  f"Reference OBJ: {self.reference_obj_filepath}",
                  f"Applied {applied}/{n_morphs} morph(s).",
                  f"Total time: {total_elapsed:.1f}s"]
        if pkg["unresolved_paths"]:
            header.append(
                f"{len(pkg['unresolved_paths'])} readScript reference(s) could not be resolved: "
                f"{pkg['unresolved_paths']}"
            )
        if jcm_skipped:
            header.append(f"{len(jcm_skipped)} joint-corrective (JCM) morph(s) skipped: {jcm_skipped}")
        if already_present:
            header.append(f"{len(already_present)} morph(s) already present, skipped: {already_present}")
        if empty_skipped:
            header.append(f"{len(empty_skipped)} morph(s) had nothing to apply: {empty_skipped}")

        _write_report(context, _REPORT_TEXT, header + ["", *report_lines])

        summary = f"Applied {applied}/{n_morphs} morph(s) from {os.path.basename(self.injection_filepath)}"
        has_warning = bool(pkg["unresolved_paths"] or empty_skipped)
        self.report(
            {'WARNING'} if has_warning else {'INFO'},
            f"{summary} — see the '{_REPORT_TEXT}' text block.",
        )
        return {'FINISHED'}
