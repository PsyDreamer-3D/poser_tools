# SPDX-License-Identifier: GPL-3.0-or-later
"""Where a library asset (Phase 7.3's index) can be used: the Asset Browser's right-click
menu and side panel, and two asset shelves in the 3D Viewport. All of them run
poser.use_morph_package on the active mesh.

Dragging is left out on purpose. A Scene stand-in has no drop target (that is why Scene was
chosen, docs/handoff-morph-injection.md "7.3 correction"), the Asset Browser has no Python hook
for a custom drop, and a shelf drag operator would only repeat the click.
"""

import bpy
from bpy_extras import asset_utils

from ..operators.useMorphPackage import package_asset_kind

_OPERATOR = "poser.use_morph_package"
_ACTIONS = {
    "injection": ("Apply to Active Mesh...", 'ADD'),
    "removal": ("Remove from Active Mesh", 'REMOVE'),
}


def _operator_button(layout, kind):
    text, icon = _ACTIONS[kind]
    layout.operator(_OPERATOR, text=text, icon=icon)


def draw_asset_browser_context_menu(self, context):
    """Appended to ASSETBROWSER_MT_context_menu; draws nothing for other libraries' assets."""
    kind = package_asset_kind(getattr(context, "asset", None))
    if kind:
        self.layout.separator()
        _operator_button(self.layout, kind)


class POSER_PT_package_asset(asset_utils.AssetBrowserPanel, bpy.types.Panel):
    """The Asset Browser's side panel, under the asset's own details: what clicking will do and to which mesh."""
    bl_region_type = 'TOOL_PROPS'
    bl_label = "Poser Morph Package"

    @classmethod
    def poll(cls, context):
        return cls.asset_browser_panel_poll(context) and bool(package_asset_kind(getattr(context, "asset", None)))

    def draw(self, context):
        layout = self.layout
        kind = package_asset_kind(context.asset)
        layout.label(text="Injection package" if kind == "injection" else "Removal package")
        obj = context.active_object
        if obj is not None and obj.type == 'MESH':
            layout.label(text=f"Mesh: {obj.name}", icon='MESH_DATA')
        else:
            layout.label(text="Select a mesh in the 3D Viewport", icon='INFO')
        _operator_button(layout, kind)


class _PackageShelf:
    bl_space_type = 'VIEW_3D'
    # Clicking is the action; a drag would have nowhere to drop.
    bl_options = {'NO_ASSET_DRAG'}
    bl_activate_operator = "POSER_OT_use_morph_package"
    filter_scene = True
    kind = ""

    @classmethod
    def poll(cls, context):
        obj = context.active_object
        return context.mode == 'OBJECT' and obj is not None and obj.type == 'MESH'

    @classmethod
    def asset_poll(cls, asset):
        return package_asset_kind(asset) == cls.kind

    @classmethod
    def draw_context_menu(cls, _context, _asset, layout):
        _operator_button(layout, cls.kind)


class VIEW3D_AST_poser_injection_packages(_PackageShelf, bpy.types.AssetShelf):
    bl_idname = "VIEW3D_AST_poser_injection_packages"
    kind = "injection"


class VIEW3D_AST_poser_removal_packages(_PackageShelf, bpy.types.AssetShelf):
    bl_idname = "VIEW3D_AST_poser_removal_packages"
    kind = "removal"


# (keymap, item) pairs added by register_menus(), removed again by unregister_menus().
_keymap_items = []


def register_menus():
    bpy.types.ASSETBROWSER_MT_context_menu.append(draw_asset_browser_context_menu)
    # Double-click in the Asset Browser does what a shelf click does. The asset API has no
    # click hook there; an add-on keymap item is the way in. The operator's poll refuses any
    # asset that isn't one of our packages, and the event then goes on to Blender's own
    # double-click items, so other libraries behave as before (probed, docs 7.4).
    keyconfig = bpy.context.window_manager.keyconfigs.addon
    if keyconfig is None:          # --background: no key configuration
        return
    keymap = keyconfig.keymaps.new(name="File Browser Main", space_type='FILE_BROWSER')
    item = keymap.keymap_items.new(_OPERATOR, 'LEFTMOUSE', 'DOUBLE_CLICK')
    _keymap_items.append((keymap, item))


def unregister_menus():
    for keymap, item in _keymap_items:
        keymap.keymap_items.remove(item)
    _keymap_items.clear()
    bpy.types.ASSETBROWSER_MT_context_menu.remove(draw_asset_browser_context_menu)
