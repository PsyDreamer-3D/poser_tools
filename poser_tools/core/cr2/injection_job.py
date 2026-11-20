# SPDX-License-Identifier: GPL-3.0-or-later
"""
injection_job.py
----------------
Everything Apply Morph Injection computes before it touches Blender, as one job a worker
thread can run while the main thread keeps the UI live (Phase 7.7,
docs/handoff-morph-injection.md). No bpy.

Measured on Aiko3 with V3's "! All Head Morphs.pz2" (259 morphs, all selected): reading the
package took 26 s and building each morph's positions 10 s, both on the main thread, around a
37 s correspondence build that Phase 5.1 had already moved to a thread. Blender froze for the
parse with no feedback. The job now does all three; the operator only reads the mesh before it
starts and adds the finished shape keys (operators/applyMorphInjection.py).

Stages, in order, each readable from the main thread while the worker runs:
  "reading"         parsing the package's files            (progress: files parsed)
  "correspondence"  loading the OBJ, reusing or building    (progress: the build's own phases)
                    the vertex correspondence
  "morphs"          building each morph's positions          (progress: morphs done)
A morph's positions are a full copy of the mesh (1.7 MB on Aiko3), so they are produced one at
a time by morphs() and handed over through a small bounded queue, never all held at once.
"""

import queue
import threading
import time

from .actor_vertex_index import load_obj_actor_vertex_groups
from .apply_injection import build_shape_key_positions
from .correspondence_cache import correspondence_key, unpack_correspondence
from .injection_package import load_injection_package
from .mesh_correspondence import build_vertex_correspondence
from .name_match import build_channel_index, display_name_for, match_channel_group, present_name_candidates
from .obj_io import load_obj_vertex_positions


class JobCancelled(Exception):
    """Raised inside the worker when the user cancelled; never escapes run()."""


class InjectionJob:
    """One Apply run's package parse, correspondence and positions, free of bpy.

    `basis_positions` is the mesh's reference shape (or its vertices when it has no shape
    keys yet) and `existing_keys` its shape-key names, both read on the main thread before
    the job starts. `cached` is the mesh's stored correspondence as (key, bytes), or None.
    `skip_name(name)` is True for a morph never applied (JCM: operators can't import
    core/functionsShapeKeys.py's test into a bpy-free module, so it is passed in).
    `selected` is the picker's internal names, None for every morph in the package.
    """

    def __init__(self, injection_filepath, reference_obj_filepath, basis_positions, existing_keys,
                 skip_name, selected=None, only_leaves=None, extra_roots=None, cached=None):
        self.injection_filepath = injection_filepath
        self.reference_obj_filepath = reference_obj_filepath
        self.basis_positions = basis_positions
        self.existing_keys = set(existing_keys)
        self.skip_name = skip_name
        self.selected = selected
        self.only_leaves = only_leaves
        self.extra_roots = extra_roots
        self.cached = cached

        self.cancelled = threading.Event()
        self.stage = "reading"
        self.progress = 0.0
        self.error = None

        self.pkg = None
        self.index = None
        self.morph_names = []
        self.missing_selected = []
        self.correspondence = None
        self.correspondence_key = None
        # Set when this job built the correspondence, so the main thread can cache it on the mesh.
        self.built_seconds = None
        self.reused = False
        self.rebuild_reason = None
        self.actor_obj_groups = None

        self.done = 0
        self.jcm_skipped = []
        self.already_present = []
        self.empty_skipped = []
        self.empty_lines = []

    # -- worker ------------------------------------------------------------------------

    def _progress(self, fraction):
        if self.cancelled.is_set():
            raise JobCancelled()
        self.progress = fraction

    def prepare(self):
        """Parse the package, pick the morphs, load the OBJ and settle the correspondence.

        Leaves `morph_names` empty when there is nothing to apply; the caller words why
        (operators' no_morphs_report).
        """
        self.stage, self.progress = "reading", 0.0
        self.pkg = load_injection_package(self.injection_filepath, extra_roots=self.extra_roots,
                                          only_leaves=self.only_leaves, progress_callback=self._progress)
        self.index = build_channel_index(self.pkg["figure"])
        by_internal = self.index["by_internal"]
        if self.selected is None:
            self.morph_names = list(by_internal)
        else:
            # The picker's scan and this parse are two readers of one format; a morph the scan
            # listed but the parser doesn't have must be said, not silently dropped.
            self.morph_names = [n for n in self.selected if n in by_internal]
            self.missing_selected = [n for n in self.selected if n not in by_internal]
        if not self.morph_names:
            return

        self.stage, self.progress = "correspondence", 0.0
        obj_verts = load_obj_vertex_positions(self.reference_obj_filepath)
        self.actor_obj_groups = load_obj_actor_vertex_groups(self.reference_obj_filepath)
        self.correspondence_key = correspondence_key(obj_verts, self.basis_positions)
        if self.cached is not None:
            key, blob = self.cached
            if key != self.correspondence_key:
                self.rebuild_reason = "the mesh or reference OBJ changed since the cached copy"
            else:
                try:
                    self.correspondence = unpack_correspondence(blob, len(obj_verts), len(self.basis_positions))
                    self.reused = True
                except ValueError as exc:
                    self.rebuild_reason = f"the cached copy was unusable: {exc}"
        if self.correspondence is None:
            started = time.time()
            self.correspondence = build_vertex_correspondence(
                obj_verts, self.basis_positions, progress_callback=self._progress)
            self.built_seconds = time.time() - started
        self._progress(1.0)

    def morphs(self):
        """Yield (internal_name, display name, build_shape_key_positions result) per morph to add.

        Skipped morphs (JCM, already on the mesh, nothing to apply) go to the job's lists
        instead. Stops early, without raising, once the job is cancelled.
        """
        self.stage, self.progress = "morphs", 0.0
        total = len(self.morph_names)
        for i, internal_name in enumerate(self.morph_names):
            if self.cancelled.is_set():
                return
            self.done, self.progress = i, i / total
            # The channel's own `name` (e.g. "BrowHeavy") names the shape key; internal_name
            # (e.g. "PBMDC_39") is only the CR2's lookup key.
            name = display_name_for(self.index, internal_name)
            if self.skip_name(name):
                self.jcm_skipped.append(name)
                continue
            # Not just `name`: the mesh may carry the morph under its old raw internal name,
            # or (from the FBX) without a leading "p".
            if any(n in self.existing_keys for n in present_name_candidates(name, internal_name)):
                self.already_present.append(name)
                continue
            result = build_shape_key_positions(match_channel_group(self.index, internal_name),
                                               self.actor_obj_groups, self.basis_positions, self.correspondence)
            if result["touched_count"] == 0:
                self.empty_skipped.append(name)
                self.empty_lines.append(
                    f"{name}: skipped, nothing to apply (pmd_deltas_skipped={result['pmd_deltas_skipped']}, "
                    f"unmapped={result['unmapped_count']})")
                continue
            yield internal_name, name, result
        self.done, self.progress = total, 1.0

    def run(self, out: queue.Queue):
        """The worker thread's body: prepare(), then feed morphs() into `out`.

        `out` is bounded, so a slow main thread holds the worker back instead of letting
        finished positions pile up. Errors land in `error`; cancelling ends it quietly.
        """
        try:
            self.prepare()
            if not self.morph_names:
                return
            for item in self.morphs():
                while True:
                    if self.cancelled.is_set():
                        return
                    try:
                        out.put(item, timeout=0.1)
                        break
                    except queue.Full:
                        continue
        except JobCancelled:
            pass
        except Exception as exc:  # reported by the main thread, which owns the UI
            self.error = exc
