# SPDX-License-Identifier: GPL-3.0-or-later

import threading
import time
import traceback

import bpy
from bpy.props import BoolProperty

from ..core import library_build
from ..core.cr2 import library_index
from ..core.library_build import library_dir
from ..core.utils import _write_report
from ..properties.poserToolsPreferences import get_library_name, get_runtime_labels, get_runtime_roots

_REPORT_TEXT = "Poser Library Index Report"

# One build at a time: two would write the same files. Reset in every exit path of the operator.
_running = False


class _Job:
    """What the worker thread and the modal timer share. The thread only ever writes plain
    attributes here; every bpy call stays on the main thread."""

    def __init__(self, roots, out_dir, binary, blender_version, force, labels=None):
        self.roots, self.out_dir, self.binary = roots, out_dir, binary
        self.labels = labels or {}
        self.blender_version, self.force = blender_version, force
        self.phase, self.fraction = "reading", 0.0
        self.cancel = False
        self.manifest = self.result = None
        self.error = ""
        self.cancelled = False
        self.seconds = 0.0

    def on_progress(self, phase, done, total):
        self.phase = phase
        self.fraction = library_build.progress_fraction(phase, done, total)

    def on_asset_progress(self, done, total):
        self.on_progress("assets", done, total)


def _work(job: _Job) -> None:
    """The whole build: classify, write the asset files, record the status. Runs on a worker
    thread interactively, inline under --background, so it must not touch bpy."""
    started = time.time()
    try:
        previous = None if job.force else library_index.load_manifest(library_build.manifest_path(job.out_dir))
        job.manifest = library_index.build_manifest(
            job.roots, previous=previous,
            signature_extra=library_index.asset_signature(job.blender_version),
            progress=job.on_progress, cancelled=lambda: job.cancel, runtime_labels=job.labels,
        )
        job.result = library_build.build_assets(
            job.manifest, previous, job.out_dir,
            command_for=lambda job_path: library_build.blender_command(job.binary, job_path),
            progress=job.on_asset_progress, cancelled=lambda: job.cancel,
        )
        job.seconds = time.time() - started
        library_build.write_status(job.out_dir, job.manifest, job.seconds)
    except library_index.BuildCancelled:
        job.cancelled = True
    except library_build.AssetBuildError as exc:
        job.error = str(exc)
    except Exception as exc:        # anything else is a bug; surface it instead of dying silently on a thread
        job.error = f"{type(exc).__name__}: {exc}\n{traceback.format_exc()}"


def _refresh_open_asset_lists(context) -> int:
    """Make every open Asset Browser and 3D Viewport asset shelf re-read the library; returns how many.

    Blender doesn't watch library folders: an open shelf keeps listing assets from files a build
    just deleted until something refreshes it (probed on 5.3, docs/handoff-morph-injection.md 7.4).
    asset.library_refresh is the only way in (no data API for it), and it acts on the asset list of
    the region it runs in, hence one call per Asset Browser and per shelf region. Its poll is False
    for a shelf that never loaded a list, which then reads fresh when first shown.

    `context` must not be inside a temporary-screen window: from the Preferences window, where
    the build is started, temp_override raises "Overriding context with an active temporary
    screen isn't supported" (owner's report, reproduced on 5.3). Call it through
    refresh_open_asset_lists_later(), whose timer runs with no window in context.
    """
    refreshed = 0
    for window in context.window_manager.windows:
        if window.screen.is_temporary:     # Preferences, file dialogs: no asset views to refresh
            continue
        for area in window.screen.areas:
            if area.type == 'FILE_BROWSER' and area.ui_type == 'ASSETS':
                region_type = 'WINDOW'
            elif area.type == 'VIEW_3D':
                region_type = 'ASSET_SHELF'
            else:
                continue
            region = next((r for r in area.regions if r.type == region_type), None)
            if region is None:
                continue
            with context.temp_override(window=window, screen=window.screen, area=area, region=region):
                if bpy.ops.asset.library_refresh.poll():
                    bpy.ops.asset.library_refresh()
                    refreshed += 1
    return refreshed


def _refresh_from_timer():
    try:
        _refresh_open_asset_lists(bpy.context)
    except (RuntimeError, TypeError) as exc:
        # The index is fine; only what is on screen may be out of date. The build's own report
        # is already out by now, so the reason goes into its text block.
        report = bpy.data.texts.get(_REPORT_TEXT)
        if report is not None:
            report.write(f"\nCouldn't refresh the open Asset Browsers and shelves ({type(exc).__name__}: {exc}). "
                         "Right-click in the Asset Browser and choose Refresh Asset Library.\n")
    return None                            # run once


def refresh_open_asset_lists_later():
    """Refresh the open asset views just after the current operator returns, outside any window."""
    bpy.app.timers.register(_refresh_from_timer, first_interval=0.0)


class OT_BuildLibraryIndex_Operator(bpy.types.Operator):
    """Index the injection and removal packages in your Poser Runtime folders as a Blender asset library"""
    bl_idname = "poser.build_library_index"
    bl_label = "Build Library Index"
    bl_options = {'INTERNAL'}

    force: BoolProperty(
        name="Full Rebuild",
        description="Ignore the previous index and rebuild everything",
        default=False,
        options={'SKIP_SAVE'},
    )

    @classmethod
    def poll(cls, context):
        return bool(get_runtime_roots(context)) and not _running

    def execute(self, context):
        global _running
        binary = bpy.app.binary_path
        if not binary:
            self.report({'ERROR'}, "Can't find the Blender executable to build the library with.")
            return {'CANCELLED'}

        self._job = _Job(get_runtime_roots(context), library_dir(), binary,
                         bpy.app.version_string, self.force, get_runtime_labels(context))
        _running = True
        if bpy.app.background:
            # Headless: nothing dispatches TIMER events, so a modal operator would hang (see
            # Apply Morph Injection, docs/handoff-morph-injection.md Phase 5.1).
            try:
                _work(self._job)
                return self._finish(context)
            finally:
                _running = False

        wm = context.window_manager
        try:
            wm.progress_begin(0, 100)
            self._start = time.time()
            self._thread = threading.Thread(target=_work, args=(self._job,), daemon=True)
            self._thread.start()
            self._timer = wm.event_timer_add(0.2, window=context.window)
            context.window.cursor_set('WAIT')
            self._show_progress(context)
            wm.modal_handler_add(self)
        except Exception:
            # Without this a failure here would leave the flag set and block every later build.
            _running = False
            raise
        return {'RUNNING_MODAL'}

    def _show_progress(self, context):
        job = self._job
        pct = int(job.fraction * 100)
        context.window_manager.progress_update(pct)
        label = library_build.PHASE_LABELS.get(job.phase, job.phase)
        hint = "cancelling..." if job.cancel else "Esc to cancel"
        context.workspace.status_text_set(
            f"Poser library index: {label} {pct}% {time.time() - self._start:.0f}s ({hint})")

    def modal(self, context, event):
        global _running
        if event.type == 'ESC' and not self._job.cancel:
            # Asked, not forced: the worker notices between files, and the child Blender process
            # is terminated, so the thread ends within moments. Waiting keeps it from outliving us.
            self._job.cancel = True
        if event.type != 'TIMER':
            return {'PASS_THROUGH'}
        self._show_progress(context)
        if self._thread.is_alive():
            return {'RUNNING_MODAL'}

        wm = context.window_manager
        wm.event_timer_remove(self._timer)
        context.workspace.status_text_set(None)
        context.window.cursor_set('DEFAULT')
        wm.progress_end()
        _running = False
        return self._finish(context)

    def _finish(self, context):
        job = self._job
        if job.cancelled:
            self.report({'WARNING'}, "Library index build cancelled. Nothing was changed.")
            return {'CANCELLED'}
        if job.error:
            first = job.error.splitlines()[0]
            self.report({'ERROR'}, f"Library index build failed: {first}")
            _write_report(context, _REPORT_TEXT, ["Library index build failed.", "", *job.error.splitlines()])
            return {'CANCELLED'}

        # On the main thread because it edits the user's preferences: the library entry is what
        # makes the files appear in the Asset Browser. Only ever done from this explicit click.
        library, action = library_build.ensure_asset_library(
            context.preferences.filepaths.asset_libraries, get_library_name(context), job.out_dir)
        _write_report(context, _REPORT_TEXT, library_build.report_lines(
            job.manifest, job.result, job.seconds, f"{library.name!r} {action} at {job.out_dir}"))

        stats = job.manifest["stats"]
        self.report({'INFO'}, (
            f"Library index built: {stats['shown']} package(s) in {stats['folders']} folder(s), "
            f"{job.result['written']} file(s) written, {job.seconds:.0f}s. "
            f"Open the Asset Browser and pick '{library.name}'. See the '{_REPORT_TEXT}' text block."))
        if not bpy.app.background:
            # Last, and deferred: this runs from the Preferences window, whose context can't be
            # overridden into another window's screen.
            refresh_open_asset_lists_later()
        return {'FINISHED'}


class OT_RemoveLibraryEntry_Operator(bpy.types.Operator):
    """Remove the Poser package library from Blender's asset libraries (the index files stay on disk)"""
    bl_idname = "poser.remove_library_entry"
    bl_label = "Remove Library Entry"
    bl_options = {'INTERNAL'}

    @classmethod
    def poll(cls, context):
        return library_build.find_asset_library(context.preferences.filepaths.asset_libraries,
                                                library_dir(create=False)) is not None

    def execute(self, context):
        name = library_build.remove_asset_library(context.preferences.filepaths.asset_libraries,
                                                  library_dir(create=False))
        self.report({'INFO'}, f"Removed '{name}' from the asset libraries. Build Library Index adds it back.")
        return {'FINISHED'}
