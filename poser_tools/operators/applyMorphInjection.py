# SPDX-License-Identifier: GPL-3.0-or-later

import os

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

        wm = context.window_manager
        wm.progress_begin(0, 100)
        report_lines = []
        applied = 0
        jcm_skipped = []
        already_present = []
        empty_skipped = []

        try:
            # The slow step -- a few minutes on a 72k-vertex figure (confirmed via
            # a real end-to-end run, docs/handoff-morph-injection.md Phase 5), and
            # it doesn't get any faster with a smaller injection package -- this
            # one call dominates regardless of how many morphs get applied after
            # it. No caching across runs in this first cut; a blocking call with a
            # progress bar matches OT_ImportPoserFBX's own precedent rather than
            # introducing new threading this add-on doesn't have anywhere else.
            correspondence = build_vertex_correspondence(obj_verts, basis_positions)
            wm.progress_update(50)

            n_morphs = len(morph_names)
            for i, name in enumerate(morph_names):
                wm.progress_update(50 + int(50 * i / n_morphs))

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
                applied += 1
                report_lines.append(
                    f"{name}: touched={result['touched_count']} "
                    f"unmapped={result['unmapped_count']} "
                    f"pmd_deltas_skipped={result['pmd_deltas_skipped']} "
                    f"collisions_resolved={result['collisions_resolved']} "
                    f"seam_collisions_skipped={result['seam_collisions_skipped']}"
                )
        finally:
            wm.progress_end()

        header = [f"Injection file: {self.injection_filepath}",
                  f"Reference OBJ: {self.reference_obj_filepath}",
                  f"Applied {applied}/{n_morphs} morph(s)."]
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
