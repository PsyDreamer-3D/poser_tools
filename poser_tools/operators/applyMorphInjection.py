# SPDX-License-Identifier: GPL-3.0-or-later

import json
import os
import threading
import time

import bpy
import numpy as np
from bpy.props import BoolProperty, CollectionProperty, StringProperty

from ..core.cr2 import runtime_index
from ..core.cr2.actor_vertex_index import load_obj_actor_vertex_groups
from ..core.cr2.apply_injection import build_shape_key_positions
from ..core.cr2.correspondence_cache import (
    correspondence_key,
    pack_correspondence,
    unpack_correspondence,
)
from ..core.cr2.cr2_parser import CR2Parser, resolve_geom_file
from ..core.cr2.injection_package import load_injection_package
from ..core.cr2.mesh_correspondence import build_vertex_correspondence
from ..core.cr2.name_match import (
    build_channel_index,
    display_name_for,
    match_channel_group,
    present_name_candidates,
)
from ..core.cr2.obj_io import load_obj_vertex_positions
from ..core.functionsShapeKeys import is_jcm_shapekey
from ..core.utils import _write_report
from ..properties.poserToolsPreferences import get_runtime_roots

_REPORT_TEXT = "Poser Morph Injection Report"
_REFERENCE_OBJ_PROP = "poser_reference_obj"
_REFERENCE_CR2_PROP = "poser_reference_cr2"
_INJECTED_MORPHS_PROP = "poser_injected_morphs"
# {"key": <correspondence_key>, "data": <packed bytes>} on the mesh. A group, so the
# Custom Properties panel shows an "Edit Value" button instead of drawing the blob.
_CORRESPONDENCE_PROP = "poser_correspondence"


def load_injected_morphs_record(obj) -> dict:
    """internal_name -> shape-key name, accumulated across every Apply run
    on this mesh. A RemDeltas.* file carries no real `name` of its own
    (Poser only needs it to match the channel it's zeroing, not to relabel
    it), so operators/removeMorphInjection.py can't recover the shape key's
    actual name from the file it was pointed at -- this record is what lets
    it anyway."""
    raw = obj.data.get(_INJECTED_MORPHS_PROP)
    if not raw:
        return {}
    try:
        return json.loads(raw).get("morphs", {})
    except (ValueError, AttributeError):
        return {}


def _save_injected_morphs_record(obj, record: dict) -> None:
    obj.data[_INJECTED_MORPHS_PROP] = json.dumps({"version": 1, "morphs": record})


# The last report this add-on's apply/picker operators made, kept so an operator that starts
# another one with bpy.ops can show it. An operator called from Python keeps its own reports
# off the UI (WARNING/INFO are not shown anywhere, ERROR becomes an exception in the caller),
# which made "Nothing to apply" and every other message from the picker vanish when it was
# started from Apply's dialog.
_last_report = None


def say(operator, types, message):
    """operator.report(), remembered for call_nested()."""
    global _last_report
    _last_report = (types, message)
    operator.report(types, message)


def call_nested(operator, op_call, execution='INVOKE_DEFAULT', **kwargs):
    """Run another operator with bpy.ops and show what it reported, from `operator`.

    `operator` must be one the UI invoked itself, so its reports are shown. An ERROR report
    raises RuntimeError out of bpy.ops; that is swallowed only when the nested operator
    reported something, since any other RuntimeError is a real failure. `execution`
    'EXEC_DEFAULT' skips the nested operator's own dialog when the caller already has its
    answers. Only operators that report through say() can have their messages passed on.
    """
    global _last_report
    _last_report = None
    try:
        result = op_call(execution, **kwargs)
    except RuntimeError:
        if _last_report is None:
            raise
        result = {'CANCELLED'}
    if _last_report is not None:
        # Left set on purpose: this operator may itself have been nested, and its caller
        # needs the same message.
        operator.report(*_last_report)
    return result


def no_morphs_report(pkg) -> tuple:
    """(report level, message) for a package that yielded no morphs.

    When the cause is files the loader couldn't find, say so: "no morphs found"
    sends the user looking at the package instead of at their Runtime folders.
    """
    unresolved = pkg["unresolved_paths"]
    if unresolved:
        return {'ERROR'}, (
            f"None of this package's {len(unresolved)} referenced file(s) could be found "
            f"(e.g. {unresolved[0]}). Add the Poser Runtime folder that contains them under "
            "Preferences > Add-ons > Poser Tools."
        )
    return {'WARNING'}, "No targetGeom morphs found in this package."


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
                    "orchestrator that readScript-references several). Used only when no "
                    "package is picked from the list above",
        subtype='FILE_PATH',
        # Not remembered between runs: execute() writes the picked package's path here, so a
        # remembered value would come back pre-filled and be a stale answer to a new pick.
        options={'SKIP_SAVE'},
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
        options={'SKIP_SAVE'},
    )
    selected_injection_name: StringProperty(
        name="Injection Package",
        description="Pick the injection package from your Poser Runtime library",
        options={'SKIP_SAVE'},
    )
    # Set by poser.pick_injection_morphs once the user has chosen (JSON lists of internal
    # names and of the leaf files they live in). Empty means "every morph in the package",
    # which is what a scripted bpy.ops call gets, unchanged from before the picker existed.
    selected_morphs: StringProperty(options={'HIDDEN', 'SKIP_SAVE'})
    selected_leaves: StringProperty(options={'HIDDEN', 'SKIP_SAVE'})
    # True only for the interactive popup, so its OK hands over to the morph picker while a
    # scripted call still applies everything. A property rather than an attribute set in
    # invoke(): properties are what survives from invoke() to execute().
    use_morph_picker: BoolProperty(options={'HIDDEN', 'SKIP_SAVE'})

    @classmethod
    def poll(cls, context):
        return context.active_object is not None and context.active_object.type == 'MESH'

    def invoke(self, context, event):
        if self.selected_morphs:
            # Arriving from the picker with the package, figure and morphs already chosen.
            return self.execute(context)

        self.use_morph_picker = True
        obj = context.active_object
        # Remembered from a prior run on this mesh, if any -- re-applying a
        # second package to the same figure shouldn't have to re-browse for
        # its own reference OBJ every time.
        self.reference_obj_filepath = obj.get(_REFERENCE_OBJ_PROP, "")

        self.cr2_library_items.clear()
        self.injection_library_items.clear()
        self.selected_cr2_name = ""
        self.selected_injection_name = ""

        # A caller that already knows the package (a library asset, operators/useMorphPackage.py)
        # passes its path; it is shown picked in the list below rather than in the path field.
        given_package = os.path.normcase(self.injection_filepath) if self.injection_filepath else ""

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
                    if given_package and os.path.normcase(lib_item.filepath) == given_package:
                        self.selected_injection_name = display_name

        if self.selected_cr2_name:
            # The remembered figure is pre-selected and its OBJ is resolved from it, so the OBJ
            # field stays empty: a pre-filled one would override a different figure picked here.
            self.reference_obj_filepath = ""
        if self.selected_injection_name:
            # Same for the package: picked in the list, so the path field would only say "ignored".
            self.injection_filepath = ""

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
        if self.selected_injection_name and self.injection_filepath:
            layout.label(text="The package picked above is used; this path is ignored.", icon='INFO')
        layout.prop(self, "reference_obj_filepath")
        if self.selected_cr2_name and self.reference_obj_filepath:
            layout.label(text="The figure picked above is used; this .obj is ignored.", icon='INFO')

    def execute(self, context):
        self._op_start_time = time.time()
        obj = context.active_object

        # What the user picked from the lists wins over the manual path fields. The lists start
        # empty every time (invoke), so a value there is a pick made in this dialog, while a
        # filled path may be left over. A call from the morph picker has already settled both.
        from_picker = bool(self.selected_morphs)

        if self.selected_injection_name and not from_picker:
            item = self.injection_library_items.get(self.selected_injection_name)
            if item is not None:
                self.injection_filepath = item.filepath
            elif not self.injection_filepath:
                say(self, {'ERROR'}, f"{self.selected_injection_name!r} doesn't match a package in your Poser library.")
                return {'CANCELLED'}

        cr2_filepath = ""
        cr2_item = None
        if self.selected_cr2_name and not from_picker:
            cr2_item = self.cr2_library_items.get(self.selected_cr2_name)
            if cr2_item is None and not self.reference_obj_filepath:
                say(self, {'ERROR'}, f"{self.selected_cr2_name!r} doesn't match a figure in your Poser library.")
                return {'CANCELLED'}
        if cr2_item is not None:
            cr2_filepath = cr2_item.filepath
            figure = CR2Parser.parse_file(cr2_filepath)
            resolve_geom_file(figure, cr2_filepath, extra_roots=get_runtime_roots(context))
            if not (figure.geom_file and os.path.isfile(figure.geom_file)):
                say(self,
                    {'ERROR'},
                    f"Could not resolve a reference OBJ from {os.path.basename(cr2_filepath)}'s figureResFile."
                )
                return {'CANCELLED'}
            self.reference_obj_filepath = figure.geom_file

        if not os.path.isfile(self.injection_filepath):
            say(self, {'ERROR'}, f"Injection file not found: {self.injection_filepath!r}")
            return {'CANCELLED'}
        if not os.path.isfile(self.reference_obj_filepath):
            say(self, {'ERROR'}, f"Reference OBJ not found: {self.reference_obj_filepath!r}")
            return {'CANCELLED'}

        # Remember the reference OBJ (and, if it came from the library
        # picker, its CR2) for next time even if the rest of this run fails
        # partway through -- it was still a correct answer.
        obj[_REFERENCE_OBJ_PROP] = self.reference_obj_filepath
        if cr2_filepath:
            obj[_REFERENCE_CR2_PROP] = cr2_filepath

        if self.use_morph_picker and not self.selected_morphs:
            # Which morphs to apply is the picker's question; it comes back here with the answer.
            call_nested(
                self, bpy.ops.poser.pick_injection_morphs,
                injection_filepath=self.injection_filepath,
                reference_obj_filepath=self.reference_obj_filepath,
            )
            return {'FINISHED'}

        try:
            selected = json.loads(self.selected_morphs) if self.selected_morphs else None
            only_leaves = json.loads(self.selected_leaves) if self.selected_leaves else None
        except ValueError as exc:
            say(self, {'ERROR'}, f"Couldn't read the selected morphs: {exc}")
            return {'CANCELLED'}

        pkg = load_injection_package(
            self.injection_filepath, extra_roots=get_runtime_roots(context), only_leaves=only_leaves
        )
        index = build_channel_index(pkg["figure"])
        if selected is None:
            morph_names = list(index["by_internal"].keys())
            self._missing_selected = []
        else:
            # The picker's scan and this parse are two readers of one format; a morph the scan
            # listed but the parser doesn't have must be said, not silently dropped.
            morph_names = [n for n in selected if n in index["by_internal"]]
            self._missing_selected = [n for n in selected if n not in index["by_internal"]]

        if not morph_names:
            if selected is not None:
                say(self, {'WARNING'}, f"None of the {len(selected)} selected morph(s) could be read from the package's files.")
            else:
                say(self, *no_morphs_report(pkg))
            return {'CANCELLED'}
        self._selection_count = None if selected is None else len(selected)

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
        self._correspondence_key = correspondence_key(obj_verts, basis_positions)
        self._rebuild_reason = None
        self._correspondence = self._cached_correspondence(
            obj, len(obj_verts), len(basis_positions)
        )
        self._thread_error = None
        # Set via progress_callback from the background thread; modal() turns
        # it into a real wm.progress_update() on the main thread.
        self._correspondence_progress = 0.0

        wm = context.window_manager
        wm.progress_begin(0, 100)
        wm.progress_update(5)

        if self._correspondence is not None:
            # Same reference OBJ and an untouched mesh: skip the ~30s build.
            self._correspondence_note = "reused from this mesh's cache"
            try:
                return self._apply_morphs(context)
            finally:
                wm.progress_end()

        if bpy.app.background:
            # Headless: no event loop to dispatch TIMER events, so going modal
            # would hang forever. `context.window` isn't a reliable headless
            # check on its own (see docs/handoff-morph-injection.md Phase 5.1).
            try:
                started = time.time()
                self._correspondence = build_vertex_correspondence(
                    obj_verts, basis_positions, progress_callback=self._set_correspondence_progress
                )
                self._remember_correspondence(obj, time.time() - started)
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

    def _cached_correspondence(self, obj, n_source, n_target):
        """The correspondence cached on the mesh if it still applies, else None.

        A key mismatch is the normal "mesh or reference OBJ changed" case. A
        blob that matches its key but can't be read is reported, then rebuilt.
        """
        entry = obj.data.get(_CORRESPONDENCE_PROP)
        if entry is None:
            return None
        try:
            if entry["key"] != self._correspondence_key:
                self._rebuild_reason = "the mesh or reference OBJ changed since the cached copy"
                return None
            return unpack_correspondence(bytes(entry["data"]), n_source, n_target)
        except (KeyError, TypeError, AttributeError, ValueError) as exc:
            say(self, {'WARNING'}, f"Ignoring the cached vertex correspondence: {exc}")
            self._rebuild_reason = f"the cached copy was unusable: {exc}"
            return None

    def _remember_correspondence(self, obj, seconds):
        """Cache a freshly built correspondence on the mesh (main thread only: touches bpy data)."""
        note = f"built in {seconds:.1f}s"
        if self._rebuild_reason:
            note += f" ({self._rebuild_reason})"
        try:
            obj.data[_CORRESPONDENCE_PROP] = {
                "key": self._correspondence_key,
                "data": pack_correspondence(self._correspondence),
            }
            note += "; cached on the mesh for next time"
        except (ValueError, TypeError, OverflowError, RuntimeError) as exc:
            say(self, {'WARNING'}, f"Couldn't cache the vertex correspondence on the mesh: {exc}")
            note += f"; NOT cached: {exc}"
        self._correspondence_note = note

    def modal(self, context, event):
        if event.type == 'ESC':
            self._finish_modal(context)
            context.window_manager.progress_end()
            # The thread isn't forcibly killed -- it finishes in the
            # background and its result is just never picked up. Safe: it
            # never touches bpy.data.
            say(self,
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
            say(self, {'ERROR'}, f"Vertex correspondence build failed: {self._thread_error}")
            return {'CANCELLED'}

        try:
            self._remember_correspondence(self._obj, time.time() - self._start_time)
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
        newly_applied = {}

        for i, internal_name in enumerate(morph_names):
            wm.progress_update(90 + int(10 * i / n_morphs))

            # The channel's own `name` property (e.g. "BrowHeavy") is what the
            # created shape key is named -- internal_name (e.g. "PBMDC_39") is
            # only the CR2's lookup key, not fit for a user-facing name.
            name = display_name_for(index, internal_name)

            if is_jcm_shapekey(name):
                jcm_skipped.append(name)
                continue
            # Not just `name`: the mesh may carry the morph under its old raw
            # internal name, or (from the FBX) without a leading "p".
            if any(n in obj.data.shape_keys.key_blocks for n in present_name_candidates(name, internal_name)):
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
            newly_applied[internal_name] = sk.name
            report_lines.append(
                f"{name}: touched={result['touched_count']} "
                f"unmapped={result['unmapped_count']} "
                f"pmd_deltas_skipped={result['pmd_deltas_skipped']} "
                f"collisions_resolved={result['collisions_resolved']} "
                f"seam_collisions_skipped={result['seam_collisions_skipped']}"
            )

        if newly_applied:
            record = load_injected_morphs_record(obj)
            record.update(newly_applied)
            _save_injected_morphs_record(obj, record)

        total_elapsed = time.time() - self._op_start_time
        header = [f"Injection file: {self.injection_filepath}",
                  f"Reference OBJ: {self.reference_obj_filepath}",
                  f"Applied {applied}/{n_morphs} morph(s).",
                  f"Total time: {total_elapsed:.1f}s",
                  f"Vertex correspondence: {self._correspondence_note}"]
        if self._selection_count is not None:
            header.append(f"Applied only the {self._selection_count} morph(s) chosen in the morph picker.")
        if self._missing_selected:
            header.append(f"{len(self._missing_selected)} chosen morph(s) were not found in the package's files: {self._missing_selected}")
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
        has_warning = bool(pkg["unresolved_paths"] or empty_skipped or self._missing_selected)
        say(self,
            {'WARNING'} if has_warning else {'INFO'},
            f"{summary} — see the '{_REPORT_TEXT}' text block.",
        )
        return {'FINISHED'}
