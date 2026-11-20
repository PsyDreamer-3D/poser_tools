# SPDX-License-Identifier: GPL-3.0-or-later

import os

import bpy

from ..core.library_build import package_kind, resolve_package_asset
from ..properties.poserToolsPreferences import get_runtime_roots
from .applyMorphInjection import _REFERENCE_OBJ_PROP, call_nested, say

# The custom keys library_assets_child.make_standin() writes on every asset.
_ASSET_KEYS = ("poser_root", "poser_rel_path", "poser_kind")


def asset_fields(asset) -> dict:
    """The asset's own Poser keys. Readable on external assets through
    AssetRepresentation.metadata (probed live on Blender 5.3, docs 7.4)."""
    metadata = asset.metadata
    return {key: metadata.get(key) or "" for key in _ASSET_KEYS}


def package_asset_kind(asset) -> str:
    """One of library_build.PACKAGE_KINDS when `asset` is one of the library's packages, else ""."""
    if asset is None or asset.id_type != 'SCENE':
        return ""
    return package_kind(asset_fields(asset), [tag.name for tag in asset.metadata.tags])


def context_asset(context):
    """The asset the user clicked. One place, so a headless check can supply one: under
    --background there is no Asset Browser and temp_override(asset=...) takes no stand-in."""
    return getattr(context, "asset", None)


class OT_UseMorphPackage_Operator(bpy.types.Operator):
    """Apply or remove the Poser morph package, or apply the MAT pose or material file, this asset stands for, on the active mesh"""
    # The idname predates MAT poses (Phase 8.2); kept, since the shelves' bl_activate_operator and
    # the Asset Browser's double-click keymap item are bound to it.
    bl_idname = "poser.use_morph_package"
    bl_label = "Use Poser Package"
    # Needs an asset in the context (Asset Browser, asset shelf), so it isn't offered in search.
    # No UNDO of its own: it only starts Apply, the picker or Remove, which push their own steps.
    bl_options = {'INTERNAL'}

    @classmethod
    def poll(cls, context):
        obj = context.active_object
        if obj is None or obj.type != 'MESH':
            cls.poll_message_set("Select the mesh to apply the package to")
            return False
        if not package_asset_kind(context_asset(context)):
            cls.poll_message_set("Pick a package from the Poser package library")
            return False
        return True

    def invoke(self, context, event):
        return self.execute(context)

    def execute(self, context):
        asset = context_asset(context)
        metadata = asset.metadata
        path, kind, problem = resolve_package_asset(
            asset_fields(asset), metadata.description, [tag.name for tag in metadata.tags],
            get_runtime_roots(context),
        )
        if problem:
            say(self, {'ERROR'}, problem)
            return {'CANCELLED'}

        obj = context.active_object
        if kind == "material":
            if not any(slot.material for slot in obj.material_slots):
                say(self, {'WARNING'}, f"{obj.name} has no materials for a MAT pose to replace.")
                return {'CANCELLED'}
            # Its dialog previews what will be replaced and is the confirmation (owner's call).
            result = call_nested(self, bpy.ops.poser.apply_mat_pose, mat_filepath=path)
            return {'CANCELLED'} if 'CANCELLED' in result else {'FINISHED'}
        if kind == "slot_material":
            slot = obj.material_slots[obj.active_material_index] if obj.material_slots else None
            if slot is None or slot.material is None:
                say(self, {'WARNING'}, f"Select the material slot on {obj.name} to apply {os.path.basename(path)} to.")
                return {'CANCELLED'}
            # Same as a MAT pose: its dialog says which material it replaces and is the confirmation.
            result = call_nested(self, bpy.ops.poser.apply_material_to_slot, mat_filepath=path)
            return {'CANCELLED'} if 'CANCELLED' in result else {'FINISHED'}
        if kind == "removal":
            if obj.data.shape_keys is None:
                say(self, {'WARNING'}, f"{obj.name} has no shape keys; nothing to remove.")
                return {'CANCELLED'}
            # Runs straight away: Remove only deletes the package's shape keys, says which in its
            # report, and is one Ctrl+Z to undo. Its dialog would only ask for the file we have.
            result = call_nested(self, bpy.ops.poser.remove_morph_injection,
                                 execution='EXEC_DEFAULT', injection_filepath=path)
        else:
            reference_obj = obj.get(_REFERENCE_OBJ_PROP, "")
            if reference_obj and os.path.isfile(reference_obj):
                # The mesh has been injected into before, so its figure is known: straight to
                # choosing morphs, the same dialog Apply's popup hands over to.
                result = call_nested(self, bpy.ops.poser.pick_injection_morphs,
                                     injection_filepath=path, reference_obj_filepath=reference_obj)
            else:
                # First package on this mesh: Apply's popup, with the package already picked, so
                # only the figure is left to choose.
                result = call_nested(self, bpy.ops.poser.apply_morph_injection, injection_filepath=path)
        return {'CANCELLED'} if 'CANCELLED' in result else {'FINISHED'}
