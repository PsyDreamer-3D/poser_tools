# SPDX-License-Identifier: GPL-3.0-or-later

import os
import threading
import time

import bpy
import numpy as np
from bpy.props import CollectionProperty, StringProperty

from ..core.cr2 import runtime_index
from ..core.cr2.actor_vertex_index import load_obj_actor_vertex_groups
from ..core.cr2.apply_injection import build_shape_key_positions
from ..core.cr2.cr2_parser import CR2Parser, resolve_geom_file
from ..core.cr2.injection_package import load_injection_package
from ..core.cr2.mesh_correspondence import build_vertex_correspondence
from ..core.cr2.name_match import build_channel_index, display_name_for, match_channel_group
from ..core.cr2.obj_io import load_obj_vertex_positions
from ..core.functionsShapeKeys import is_jcm_shapekey
from ..core.utils import _write_report
from ..properties.poserToolsPreferences import get_runtime_roots

_REPORT_TEXT = "Poser Morph Injection Report"
_REFERENCE_OBJ_PROP = "poser_reference_obj"
_REFERENCE_CR2_PROP = "poser_reference_cr2"


class PoserLibraryItem(bpy.types.PropertyGroup):
    """One Runtime-indexed CR2/PZ2 file for the searchable pickers below.

    name is category-qualified ("!DAZ/A3-H3MorExp1/Deltas/InjDeltas.Foo.pz2"),
    not a bare filename -- DAZ products commonly reuse orchestrator names like
    "! All Morphs.pz2" across different product folders, so prop_search()
    entries need the folder path to stay unique and disambiguated.
    """
    name: StringProperty()
    filepath: StringProperty()


def _library_item_display_name(item: runtime_index.LibraryItem) -> str:
    stem = f"{item.name}.{item.ext}"
    return f"{item.category}/{stem}" if item.category else stem


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
    cr2_library_items: CollectionProperty(type=PoserLibraryItem)
    injection_library_items: CollectionProperty(type=PoserLibraryItem)
    selected_cr2_name: StringProperty(
        name="Figure (CR2)",
        description="Pick the figure from your Poser Runtime library -- its reference OBJ is "
                    "resolved automatically from the CR2's own figureResFile",
    )
    selected_injection_name: StringProperty(
        name="Injection Package",
        description="Pick the injection package from your Poser Runtime library",
    )

    @classmethod
    def poll(cls, context):
        return context.active_object is not None and context.active_object.type == 'MESH'

    def invoke(self, context, event):
        obj = context.active_object
        # Remembered from a prior run on this mesh, if any -- re-applying a
        # second package to the same figure shouldn't have to re-browse for
        # its own reference OBJ every time.
        self.reference_obj_filepath = obj.get(_REFERENCE_OBJ_PROP, "")

        self.cr2_library_items.clear()
        self.injection_library_items.clear()
        self.selected_cr2_name = ""
        self.selected_injection_name = ""

        roots = get_runtime_roots(context)
        if roots:
            remembered_cr2 = obj.get(_REFERENCE_CR2_PROP, "")
            for lib_item in runtime_index.get_all_items(roots):
                display_name = _library_item_display_name(lib_item)
                if lib_item.ext in ("cr2", "crz"):
                    entry = self.cr2_library_items.add()
                    entry.name = display_name
                    entry.filepath = lib_item.filepath
                    if remembered_cr2 and os.path.normcase(lib_item.filepath) == os.path.normcase(remembered_cr2):
                        self.selected_cr2_name = display_name
                elif lib_item.ext in ("pz2", "p2z"):
                    entry = self.injection_library_items.add()
                    entry.name = display_name
                    entry.filepath = lib_item.filepath

        return context.window_manager.invoke_props_dialog(self, width=500)

    def draw(self, context):
        layout = self.layout
        layout.use_property_split = True
        layout.use_property_decorate = False

        if self.cr2_library_items or self.injection_library_items:
            layout.label(text="From your Poser Runtime library:")
            if self.cr2_library_items:
                layout.prop_search(self, "selected_cr2_name", self, "cr2_library_items")
            if self.injection_library_items:
                layout.prop_search(self, "selected_injection_name", self, "injection_library_items")
            layout.separator()
            layout.label(text="Or specify paths manually:")
        else:
            layout.label(text="No Poser Runtime folders configured -- see Preferences.", icon='INFO')

        layout.prop(self, "injection_filepath")
        layout.prop(self, "reference_obj_filepath")

    def execute(self, context):
        self._op_start_time = time.time()
        obj = context.active_object

        if not self.injection_filepath and self.selected_injection_name:
            item = self.injection_library_items.get(self.selected_injection_name)
            if item is None:
                self.report({'ERROR'}, f"{self.selected_injection_name!r} doesn't match a package in your Poser library.")
                return {'CANCELLED'}
            self.injection_filepath = item.filepath

        cr2_filepath = ""
        if not self.reference_obj_filepath and self.selected_cr2_name:
            item = self.cr2_library_items.get(self.selected_cr2_name)
            if item is None:
                self.report({'ERROR'}, f"{self.selected_cr2_name!r} doesn't match a figure in your Poser library.")
                return {'CANCELLED'}
            cr2_filepath = item.filepath
            figure = CR2Parser.parse_file(cr2_filepath)
            resolve_geom_file(figure, cr2_filepath, extra_roots=get_runtime_roots(context))
            if not (figure.geom_file and os.path.isfile(figure.geom_file)):
                self.report(
                    {'ERROR'},
                    f"Could not resolve a reference OBJ from {os.path.basename(cr2_filepath)}'s figureResFile."
                )
                return {'CANCELLED'}
            self.reference_obj_filepath = figure.geom_file

        if not os.path.isfile(self.injection_filepath):
            self.report({'ERROR'}, f"Injection file not found: {self.injection_filepath!r}")
            return {'CANCELLED'}
        if not os.path.isfile(self.reference_obj_filepath):
            self.report({'ERROR'}, f"Reference OBJ not found: {self.reference_obj_filepath!r}")
            return {'CANCELLED'}

        # Remember the reference OBJ (and, if it came from the library
        # picker, its CR2) for next time even if the rest of this run fails
        # partway through -- it was still a correct answer.
        obj[_REFERENCE_OBJ_PROP] = self.reference_obj_filepath
        if cr2_filepath:
            obj[_REFERENCE_CR2_PROP] = cr2_filepath

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
        # Set via progress_callback from the background thread; modal() turns
        # it into a real wm.progress_update() on the main thread.
        self._correspondence_progress = 0.0

        wm = context.window_manager
        wm.progress_begin(0, 100)
        wm.progress_update(5)

        if bpy.app.background:
            # Headless: no event loop to dispatch TIMER events, so going modal
            # would hang forever. `context.window` isn't a reliable headless
            # check on its own (see docs/handoff-morph-injection.md Phase 5.1).
            try:
                self._correspondence = build_vertex_correspondence(
                    obj_verts, basis_positions, progress_callback=self._set_correspondence_progress
                )
                return self._apply_morphs(context)
            finally:
                wm.progress_end()

        # Interactive: run the correspondence build on a background thread so
        # a modal timer can keep Blender's UI responsive (see
        # docs/handoff-morph-injection.md Phase 5.1 for why this is needed).
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
        # Status text alone was confirmed too easy to miss -- the wait cursor
        # is a harder-to-miss "busy" signal.
        context.window.cursor_set('WAIT')
        wm.modal_handler_add(self)
        return {'RUNNING_MODAL'}

    def _set_correspondence_progress(self, fraction):
        self._correspondence_progress = fraction

    def modal(self, context, event):
        if event.type == 'ESC':
            self._finish_modal(context)
            context.window_manager.progress_end()
            # The thread isn't forcibly killed -- it finishes in the
            # background and its result is just never picked up. Safe: it
            # never touches bpy.data.
            self.report(
                {'WARNING'},
                "Apply Morph Injection cancelled -- no shape keys were added.",
            )
            return {'CANCELLED'}

        if event.type != 'TIMER':
            return {'PASS_THROUGH'}

        elapsed = time.time() - self._start_time
        # 5-90%: correspondence build; 90-100%: per-morph loop below.
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

        for i, internal_name in enumerate(morph_names):
            wm.progress_update(90 + int(10 * i / n_morphs))

            # The channel's own `name` property (e.g. "BrowHeavy") is what the
            # created shape key is named -- internal_name (e.g. "PBMDC_39") is
            # only the CR2's lookup key, not fit for a user-facing name.
            name = display_name_for(index, internal_name)

            if is_jcm_shapekey(name):
                jcm_skipped.append(name)
                continue
            # Check internal_name too: a mesh from before this naming change
            # may already carry the morph under its old raw-internal-name key.
            if name in obj.data.shape_keys.key_blocks or internal_name in obj.data.shape_keys.key_blocks:
                already_present.append(name)
                continue

            group = match_channel_group(index, internal_name)
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
            # shape_key_add() defaults value to 1.0 -- rest at 0 like a
            # Poser/DAZ dial instead.
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
