# SPDX-License-Identifier: GPL-3.0-or-later

import os
import time

import bpy
from bpy.props import CollectionProperty, IntProperty, StringProperty

# Package-relative so this resolves correctly whether Blender loaded
# poser_tools as a legacy add-on or a namespaced extension -- never hardcode
# "poser_tools" here.
_ADDON_PKG = __package__.rsplit('.', 1)[0]


class PoserRuntimePathItem(bpy.types.PropertyGroup):
    """One registered Poser Runtime root -- the folder that *contains* a
    Runtime folder, same convention core/cr2/poser_paths.py already uses."""
    path: StringProperty(
        name="Runtime Root",
        description="Folder that contains a Poser Runtime directory",
        subtype='DIR_PATH',
    )
    label: StringProperty(
        name="Label",
        description="Optional friendly name shown in the path list (leave blank to use the folder name)",
        default="",
    )


def _rename_registered_library(self, context):
    # Only an entry already registered is renamed; an unbuilt library gets the name when it is built.
    # Never creates one: registering edits the user's asset libraries, which only Build does.
    from ..core import library_build
    library = library_build.find_asset_library(context.preferences.filepaths.asset_libraries,
                                               library_build.library_dir(create=False))
    if library is not None:
        library_build.ensure_asset_library(context.preferences.filepaths.asset_libraries,
                                           self.library_name, library.path)


class PoserToolsPreferences(bpy.types.AddonPreferences):
    bl_idname = _ADDON_PKG

    runtime_paths: CollectionProperty(type=PoserRuntimePathItem)
    runtime_paths_index: IntProperty(default=0)
    library_name: StringProperty(
        name="Library Name",
        description="The name the package library has in the Asset Browser's library list "
                    "(blank means \"Poser\")",
        default="Poser",
        update=_rename_registered_library,
    )

    def draw(self, context):
        layout = self.layout

        layout.label(text="Poser Runtime Folders:")
        row = layout.row()
        row.template_list(
            "POSER_UL_runtime_paths", "runtime_paths",
            self, "runtime_paths",
            self, "runtime_paths_index",
        )
        col = row.column(align=True)
        col.operator("poser.runtime_root_add", icon='ADD', text="")
        col.operator("poser.runtime_root_remove", icon='REMOVE', text="")
        col.separator()
        op_up = col.operator("poser.runtime_root_move", icon='TRIA_UP', text="")
        op_up.direction = "UP"
        op_down = col.operator("poser.runtime_root_move", icon='TRIA_DOWN', text="")
        op_down.direction = "DOWN"

        if self.runtime_paths and 0 <= self.runtime_paths_index < len(self.runtime_paths):
            sel = self.runtime_paths[self.runtime_paths_index]
            layout.prop(sel, "path")
            layout.prop(sel, "label", placeholder="Label (optional)")
            if sel.path and not os.path.isdir(sel.path):
                warn = layout.row()
                warn.alert = True
                warn.label(text="Path not found", icon='ERROR')

        layout.separator()
        layout.operator("poser.runtime_roots_import_from_poser", icon='IMPORT')

        self._draw_library(context, layout)

    def _draw_library(self, context, layout):
        # Imported here so the Preferences panel still draws if the build code ever fails to load.
        from ..core import library_build

        layout.separator()
        box = layout.box()
        box.label(text="Package Library", icon='ASSET_MANAGER')
        box.label(text="Index your injection and removal packages and MAT poses as a Blender asset library, with thumbnails.")
        box.prop(self, "library_name")

        roots = get_runtime_roots(context)
        status = library_build.read_status(library_build.library_dir(create=False))
        if not roots:
            box.label(text="Add a Poser Runtime folder above first.", icon='INFO')
        if status:
            stats = status["stats"]
            built = time.strftime("%Y-%m-%d %H:%M", time.localtime(status["built_at"]))
            box.label(text=f"{stats['shown']:,} packages in {stats['folders']} folders, built {built}")
            thumbs = stats.get("thumbnails", {})
            # .rsr thumbnails are decoded since 7.3d (a damaged one still gets a placeholder; the
            # build report counts those).
            box.label(text=f"{thumbs.get('image', 0) + thumbs.get('rsr', 0):,} thumbnails "
                           f"({thumbs.get('rsr', 0):,} from old Mac .rsr files), "
                           f"{thumbs.get('none', 0):,} placeholders")
            if library_build.status_is_stale(status, roots, bpy.app.version_string,
                                                get_runtime_labels(context)):
                warn = box.row()
                warn.alert = True
                warn.label(text="Runtime folders, their labels or the Blender version changed: update the index.", icon='ERROR')
        else:
            box.label(text="Not built yet. The first build takes up to a minute on a slow drive.")

        row = box.row()
        row.operator("poser.build_library_index", text="Update Library Index" if status else "Build Library Index",
                     icon='FILE_REFRESH')
        full = row.operator("poser.build_library_index", text="Full Rebuild")
        full.force = True

        libraries = context.preferences.filepaths.asset_libraries
        registered = library_build.find_asset_library(libraries, library_build.library_dir(create=False))
        row = box.row()
        if registered is not None:
            wanted = library_build.library_name(self.library_name)
            if registered.name == wanted:
                note = ""
            elif registered.name.startswith(wanted + "."):
                note = f" ('{wanted}' is taken by another library)"   # Blender added .001
            else:
                note = f" (becomes '{wanted}' on the next update)"     # e.g. the old "Poser Morph Packages"
            row.label(text=f"Asset library '{registered.name}' is registered.{note}", icon='CHECKMARK')
            row.operator("poser.remove_library_entry", icon='X')
        elif status:
            row.label(text="Built, but not registered as an asset library: update the index to add it.", icon='INFO')


def get_library_name(context) -> str:
    """The asset library's name from Preferences (library_build.library_name() fills in a blank)."""
    try:
        return context.preferences.addons[_ADDON_PKG].preferences.library_name
    except (KeyError, AttributeError):
        return ""


def get_runtime_labels(context) -> dict:
    """{root path: Label} for the roots that have one (the library index names catalogs by them)."""
    try:
        prefs = context.preferences.addons[_ADDON_PKG].preferences
        return {item.path: item.label for item in prefs.runtime_paths if item.path and item.label}
    except (KeyError, AttributeError):
        return {}


def get_runtime_roots(context) -> list:
    """Return the list of registered Runtime root paths from add-on preferences."""
    try:
        prefs = context.preferences.addons[_ADDON_PKG].preferences
        return [item.path for item in prefs.runtime_paths if item.path]
    except (KeyError, AttributeError):
        return []
