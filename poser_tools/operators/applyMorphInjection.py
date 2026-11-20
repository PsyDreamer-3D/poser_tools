# SPDX-License-Identifier: GPL-3.0-or-later

import json
import os
import queue
import threading
import time

import bpy
import numpy as np
from bpy.props import BoolProperty, CollectionProperty, StringProperty

from ..core.cr2 import runtime_index
from ..core.cr2.correspondence_cache import pack_correspondence
from ..core.cr2.cr2_parser import Figure, read_figure_res_file, resolve_geom_file
from ..core.cr2.injection_job import InjectionJob
from ..core.cr2.injection_package import match_dials_to_shape_keys, package_dials
from ..core.cr2.name_match import display_name_for, present_name_candidates
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
# The job's stages (core/cr2/injection_job.py) as status-bar text and as their share of the
# progress percentage. The correspondence share is a guess that fits a first run, when it is
# built (37 s of 76 s on V3's head package); on a cached mesh it passes in a moment.
_STAGE_TEXT = {"reading": "reading the package", "correspondence": "matching the reference OBJ to the mesh",
               "morphs": "building morphs"}
_STAGE_SPAN = {"reading": (0, 30), "correspondence": (30, 75), "morphs": (75, 100)}


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
            # Only figureResFile is needed: a full parse of a base figure's CR2 froze this OK for ~6 s.
            figure = Figure(geom_file=read_figure_res_file(cr2_filepath))
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
        if selected == []:
            # From the picker when every morph is already on the mesh but the package still dials
            # values (a character preset applied again): set those alone, nothing to parse or match.
            return self._apply_dials_only(context, obj)

        # Everything the job needs from the mesh is read here, on the main thread: the worker
        # never touches bpy.
        keys = obj.data.shape_keys
        if keys is not None:
            reference = keys.reference_key
            basis_positions = np.empty((len(reference.data), 3), dtype=np.float64)
            reference.data.foreach_get('co', basis_positions.ravel())
        else:
            # Basis is only created once a morph is actually added (_add_morph), so a package
            # with nothing to apply leaves the mesh alone. A new Basis copies the vertices.
            basis_positions = np.empty((len(obj.data.vertices), 3), dtype=np.float64)
            obj.data.vertices.foreach_get('co', basis_positions.ravel())
        cached, cache_problem = None, None
        entry = obj.data.get(_CORRESPONDENCE_PROP)
        if entry is not None:
            try:
                cached = (entry["key"], bytes(entry["data"]))
            except (KeyError, TypeError, AttributeError, ValueError) as exc:
                say(self, {'WARNING'}, f"Ignoring the cached vertex correspondence: {exc}")
                cache_problem = f"the cached copy was unusable: {exc}"

        self._obj = obj
        self._selection_count = None if selected is None else len(selected)
        self._added = []            # shape keys this run added, removed again if it's cancelled
        self._created_basis = False
        self._newly_applied = {}
        self._report_lines = []
        self._cache_stored = False
        self._correspondence_note = "reused from this mesh's cache"
        self._adding = False
        self._job = InjectionJob(
            self.injection_filepath, self.reference_obj_filepath, basis_positions,
            keys.key_blocks.keys() if keys is not None else [], is_jcm_shapekey,
            selected=selected, only_leaves=only_leaves, extra_roots=get_runtime_roots(context), cached=cached,
        )
        self._job.rebuild_reason = cache_problem

        wm = context.window_manager
        wm.progress_begin(0, 100)

        if bpy.app.background:
            # Headless: no event loop to dispatch TIMER events, so going modal would hang
            # forever. `context.window` isn't a reliable headless check on its own (see
            # docs/handoff-morph-injection.md Phase 5.1). The same job runs inline.
            try:
                job = self._job
                job.prepare()
                if not job.morph_names:
                    return self._nothing_to_apply()
                self._store_correspondence()
                for item in job.morphs():
                    self._add_morph(item)
                return self._finish_apply(context)
            finally:
                wm.progress_end()

        # Interactive: the whole job runs on a worker thread while a modal timer keeps
        # Blender's UI live, shows progress and adds each finished morph (Phase 7.7: the
        # package parse alone froze Blender for 26 s on V3's 259 head morphs).
        self._queue = queue.Queue(maxsize=4)
        self._thread = threading.Thread(target=self._job.run, args=(self._queue,), daemon=True)
        self._thread.start()
        self._timer = wm.event_timer_add(0.15, window=context.window)
        self._update_status(context)
        # Status text alone was confirmed too easy to miss -- the wait cursor
        # is a harder-to-miss "busy" signal.
        context.window.cursor_set('WAIT')
        wm.modal_handler_add(self)
        return {'RUNNING_MODAL'}

    def _set_package_dials(self, context, obj, index) -> tuple:
        """Set every shape key the package dials to the package's value: (report lines, how many).

        Poser applies a character package's dials once its morphs are loaded (Lady Xtaci's
        `MOR INJ.pz2` sets 59), so without this the morphs arrive but the character doesn't.
        Includes morphs that were already on the mesh, the FBX-baked ones too (owner's call,
        2026-11-18). Scale and bone settings are reported, never applied.
        """
        dials = package_dials(self.injection_filepath, extra_roots=get_runtime_roots(context))
        if obj.data.shape_keys is None or not (dials["dials"] or dials["other"]):
            return [], 0
        labels = {name: display_name_for(index, name) for name in index["by_internal"]} if index else {}
        keys = obj.data.shape_keys.key_blocks
        matched = match_dials_to_shape_keys(dials["dials"], keys.keys(), load_injected_morphs_record(obj), labels)

        clamped = []
        for name, value in matched["values"].items():
            # Poser dials run past 0..1 (V3's BreastSize8 is -1.012). A shape key's value is held
            # inside its slider range, so the range is widened; Blender caps it at -10..10.
            target = min(max(value, -10.0), 10.0)
            if target != value:
                clamped.append(f"{name} ({value:g})")
            key = keys[name]
            key.slider_min = min(key.slider_min, target)
            key.slider_max = max(key.slider_max, target)
            key.value = target

        lines = []
        if matched["values"]:
            lines.append(f"Set {len(matched['values'])} morph value(s) the package dials: "
                         + ", ".join(f"{name}={value:g}" for name, value in matched["values"].items()))
        if matched["missing"]:
            lines.append(f"{len(matched['missing'])} dialed morph(s) aren't on this mesh, left alone: {matched['missing']}")
        if matched["conflicts"]:
            lines.append("Dialed differently on two actors, first value kept: "
                         + ", ".join(f"{name} ({kept:g}, not {other:g})" for name, kept, other in matched["conflicts"]))
        if clamped:
            lines.append(f"Outside Blender's -10..10 slider range, clamped: {clamped}")
        if dials["other"]:
            lines.append(f"{len(dials['other'])} non-morph setting(s) not applied (scaling and posing aren't "
                         "supported): " + ", ".join(f"{actor} {name or kind}={value:g}"
                                                    for actor, name, kind, value in dials["other"]))
        return lines, len(matched["values"])

    def _apply_dials_only(self, context, obj):
        lines, dials_set = self._set_package_dials(context, obj, None)
        name = os.path.basename(self.injection_filepath)
        _write_report(context, _REPORT_TEXT, [
            f"Injection file: {self.injection_filepath}",
            "Every morph in this package was already on the mesh; only its dial values were set.",
            *lines])
        if not dials_set:
            say(self, {'WARNING'}, f"Nothing to apply from {name}: its morphs are already on the mesh, "
                                   f"and none of the morphs it dials are. See the '{_REPORT_TEXT}' text block.")
            return {'CANCELLED'}
        say(self, {'INFO'}, f"Set {dials_set} morph value(s) from {name} (its morphs were already on the mesh) "
                            f"— see the '{_REPORT_TEXT}' text block.")
        return {'FINISHED'}

    def _store_correspondence(self):
        """Cache a correspondence the job built on the mesh (main thread only: touches bpy data)."""
        job = self._job
        if job.built_seconds is None or self._cache_stored:
            return
        self._cache_stored = True
        note = f"built in {job.built_seconds:.1f}s"
        if job.rebuild_reason:
            note += f" ({job.rebuild_reason})"
        try:
            self._obj.data[_CORRESPONDENCE_PROP] = {
                "key": job.correspondence_key,
                "data": pack_correspondence(job.correspondence),
            }
            note += "; cached on the mesh for next time"
        except (ValueError, TypeError, OverflowError, RuntimeError) as exc:
            say(self, {'WARNING'}, f"Couldn't cache the vertex correspondence on the mesh: {exc}")
            note += f"; NOT cached: {exc}"
        self._correspondence_note = note

    def _update_status(self, context):
        job = self._job
        low, high = _STAGE_SPAN[job.stage]
        pct = low + int((high - low) * job.progress)
        context.window_manager.progress_update(pct)
        text = _STAGE_TEXT[job.stage]
        if job.stage == "morphs":
            text += f" {job.done}/{len(job.morph_names)}"
        context.workspace.status_text_set(
            f"Poser Morph Injection: {text}... {pct}% {time.time() - self._op_start_time:.0f}s (Esc to cancel)"
        )

    def _object_alive(self) -> bool:
        try:
            self._obj.name
        except ReferenceError:
            return False
        return True

    def modal(self, context, event):
        job = self._job
        if event.type == 'ESC':
            # The worker isn't killed: it sees the flag at its next file or morph and stops.
            # It never touches bpy data, so nothing it is still doing can harm the file.
            job.cancelled.set()
            self._roll_back()
            self._end_modal(context)
            say(self, {'WARNING'}, "Apply Morph Injection cancelled -- no shape keys were added.")
            return {'CANCELLED'}

        if event.type != 'TIMER':
            # While the package is read and the correspondence built, nothing touches the mesh,
            # so the viewport stays usable. Once shape keys are being added, input is held back
            # so the mesh can't be edited, or put in Edit Mode, between two of them.
            return {'RUNNING_MODAL'} if self._adding else {'PASS_THROUGH'}

        if not self._object_alive():
            job.cancelled.set()
            self._end_modal(context)
            say(self, {'ERROR'}, "The mesh was deleted before its morphs were added; nothing was applied.")
            return {'CANCELLED'}

        self._store_correspondence()
        # Add finished morphs for at most a tenth of a second per tick, so the window keeps
        # redrawing while a big package's 2-3 s of shape-key writes go in.
        deadline = time.time() + 0.1
        while time.time() < deadline:
            try:
                item = self._queue.get_nowait()
            except queue.Empty:
                break
            if not self._adding:
                if self._obj.mode == 'EDIT':
                    # Shape keys added from Python while the mesh is in Edit Mode are lost
                    # when it leaves Edit Mode.
                    job.cancelled.set()
                    self._end_modal(context)
                    say(self, {'ERROR'}, "The mesh was put in Edit Mode while its morphs were being "
                                         "prepared; nothing was applied. Run it again from Object Mode.")
                    return {'CANCELLED'}
                self._adding = True
            self._add_morph(item)
        self._update_status(context)

        if self._thread.is_alive() or not self._queue.empty():
            return {'RUNNING_MODAL'}

        self._end_modal(context)
        if job.error is not None:
            self._roll_back()
            say(self, {'ERROR'}, f"Apply Morph Injection failed while {_STAGE_TEXT[job.stage]}: {job.error}")
            return {'CANCELLED'}
        if not job.morph_names:
            return self._nothing_to_apply()
        return self._finish_apply(context)

    def _end_modal(self, context):
        context.window_manager.event_timer_remove(self._timer)
        context.window_manager.progress_end()
        context.workspace.status_text_set(None)
        context.window.cursor_set('DEFAULT')

    def _nothing_to_apply(self):
        job = self._job
        if job.selected is not None:
            say(self, {'WARNING'}, f"None of the {len(job.selected)} selected morph(s) could be read from the package's files.")
        else:
            say(self, *no_morphs_report(job.pkg))
        return {'CANCELLED'}

    def _add_morph(self, item):
        internal_name, name, result = item
        obj = self._obj
        if obj.data.shape_keys is None:
            obj.shape_key_add(name='Basis', from_mix=False)
            self._created_basis = True
        # Checked again here, not only by the job: two morphs of one package can share a label,
        # and the job only saw the keys the mesh had when it started.
        if any(n in obj.data.shape_keys.key_blocks for n in present_name_candidates(name, internal_name)):
            self._job.already_present.append(name)
            return
        sk = obj.shape_key_add(name=name, from_mix=False)
        sk.data.foreach_set('co', result["positions"].ravel())
        # shape_key_add() defaults value to 1.0 -- rest at 0 like a Poser/DAZ dial instead.
        sk.value = 0.0
        self._added.append(sk.name)
        self._newly_applied[internal_name] = sk.name
        self._report_lines.append(
            f"{name}: touched={result['touched_count']} "
            f"unmapped={result['unmapped_count']} "
            f"pmd_deltas_skipped={result['pmd_deltas_skipped']} "
            f"collisions_resolved={result['collisions_resolved']} "
            f"seam_collisions_skipped={result['seam_collisions_skipped']}"
        )

    def _roll_back(self):
        """Remove the shape keys this run added (and a Basis it created), for cancel and failure."""
        if not self._object_alive() or self._obj.data.shape_keys is None:
            return
        obj = self._obj
        for name in reversed(self._added):
            key = obj.data.shape_keys.key_blocks.get(name)
            if key is not None:
                obj.shape_key_remove(key)
        if self._created_basis and obj.data.shape_keys is not None and len(obj.data.shape_keys.key_blocks) == 1:
            obj.shape_key_remove(obj.data.shape_keys.key_blocks[0])
        self._added.clear()

    def _finish_apply(self, context):
        obj = self._obj
        job = self._job
        pkg = job.pkg
        applied = len(self._added)
        n_morphs = len(job.morph_names)

        if self._newly_applied:
            record = load_injected_morphs_record(obj)
            record.update(self._newly_applied)
            _save_injected_morphs_record(obj, record)
        dial_lines, dials_set = self._set_package_dials(context, obj, job.index)

        total_elapsed = time.time() - self._op_start_time
        header = [f"Injection file: {self.injection_filepath}",
                  f"Reference OBJ: {self.reference_obj_filepath}",
                  f"Applied {applied}/{n_morphs} morph(s).",
                  f"Total time: {total_elapsed:.1f}s",
                  f"Vertex correspondence: {self._correspondence_note}"]
        if self._selection_count is not None:
            header.append(f"Applied only the {self._selection_count} morph(s) chosen in the morph picker.")
        if job.missing_selected:
            header.append(f"{len(job.missing_selected)} chosen morph(s) were not found in the package's files: {job.missing_selected}")
        if pkg["unresolved_paths"]:
            header.append(
                f"{len(pkg['unresolved_paths'])} readScript reference(s) could not be resolved: "
                f"{pkg['unresolved_paths']}"
            )
        if job.jcm_skipped:
            header.append(f"{len(job.jcm_skipped)} joint-corrective (JCM) morph(s) skipped: {job.jcm_skipped}")
        if job.already_present:
            header.append(f"{len(job.already_present)} morph(s) already present, skipped: {job.already_present}")
        if job.empty_skipped:
            header.append(f"{len(job.empty_skipped)} morph(s) had nothing to apply: {job.empty_skipped}")
        header.extend(dial_lines)

        _write_report(context, _REPORT_TEXT, header + ["", *job.empty_lines, *self._report_lines])

        summary = f"Applied {applied}/{n_morphs} morph(s) from {os.path.basename(self.injection_filepath)}"
        if dials_set:
            summary += f", set {dials_set} morph value(s)"
        has_warning = bool(pkg["unresolved_paths"] or job.empty_skipped or job.missing_selected)
        say(self,
            {'WARNING'} if has_warning else {'INFO'},
            f"{summary} — see the '{_REPORT_TEXT}' text block.",
        )
        return {'FINISHED'}
