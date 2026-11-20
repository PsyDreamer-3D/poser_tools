# SPDX-License-Identifier: GPL-3.0-or-later
"""Where a library asset (Phase 7.3's index) can be used: the Asset Browser's right-click
menu, side panel and double-click, and the Poser Library asset shelf in the 3D Viewport (one kind
at a time: injection, removal or MAT poses). All of them run poser.use_morph_package on the
active mesh.

Dragging is left out on purpose. A Scene stand-in has no drop target (that is why Scene was
chosen, docs/handoff-morph-injection.md "7.3 correction"), the Asset Browser has no Python hook
for a custom drop, and a shelf drag operator would only repeat the click.
"""

import bpy
from bpy_extras import asset_utils

from ..core.utils import _TAB

from ..operators.useMorphPackage import package_asset_kind

_OPERATOR = "poser.use_morph_package"
_ACTIONS = {
    "injection": ("Apply to Active Mesh...", 'ADD'),
    "removal": ("Remove from Active Mesh", 'REMOVE'),
    "material": ("Apply Materials to Active Mesh...", 'MATERIAL'),
    "slot_material": ("Apply to a Material of Active Mesh...", 'MATERIAL'),
}
_KIND_LABELS = {"injection": "Injection package", "removal": "Removal package",
                "material": "MAT pose or material collection", "slot_material": "Single material"}
# The shelf's Materials switch lists every material kind.
_SHELF_GROUPS = {"material": ("material", "slot_material")}


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
        layout.label(text=_KIND_LABELS[kind])
        obj = context.active_object
        if obj is not None and obj.type == 'MESH':
            layout.label(text=f"Mesh: {obj.name}", icon='MESH_DATA')
        else:
            layout.label(text="Select a mesh in the 3D Viewport", icon='INFO')
        _operator_button(layout, kind)


class VIEW3D_AST_poser_packages(bpy.types.AssetShelf):
    """The Poser Library shelf: one kind of package at a time, picked with poser_shelf_kind.

    One shelf, not one per kind: Blender's 3D View shows a single asset shelf and offers no way to
    switch between shelves that poll alike, so with three only the first ever showed (owner's
    report, 8.2; probed on 5.3). A filter read in asset_poll re-filters on the next redraw.
    """
    bl_idname = "VIEW3D_AST_poser_packages"
    bl_space_type = 'VIEW_3D'
    # Clicking is the action; a drag would have nowhere to drop.
    bl_options = {'NO_ASSET_DRAG'}
    bl_activate_operator = "POSER_OT_use_morph_package"
    filter_scene = True

    @classmethod
    def poll(cls, context):
        obj = context.active_object
        return context.mode == 'OBJECT' and obj is not None and obj.type == 'MESH'

    @classmethod
    def asset_poll(cls, asset):
        kind = package_asset_kind(asset)
        wanted = bpy.context.window_manager.poser_shelf_kind
        return bool(kind) and (wanted in ("all", kind) or kind in _SHELF_GROUPS.get(wanted, ()))

    @classmethod
    def draw_context_menu(cls, _context, asset, layout):
        kind = package_asset_kind(asset)
        if kind:
            _operator_button(layout, kind)


def _draw_shelf_kind(layout, context):
    layout.prop(context.window_manager, "poser_shelf_kind", expand=True)


def draw_shelf_display_settings(self, context):
    """Appended to ASSETSHELF_PT_display, the popover behind the shelf header's settings icon."""
    shelf = getattr(context, "asset_shelf", None)
    if shelf is not None and shelf.bl_idname == VIEW3D_AST_poser_packages.bl_idname:
        self.layout.separator()
        _draw_shelf_kind(self.layout, context)


class POSER_PT_library_shelf(bpy.types.Panel):
    bl_idname = "VIEW_3D_PT_poser_library_shelf"
    bl_label = "Poser Library Shelf"
    bl_category = _TAB
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_options = {'DEFAULT_CLOSED'}

    def draw(self, context):
        layout = self.layout
        layout.label(text="The asset shelf lists:")
        _draw_shelf_kind(layout, context)
        layout.label(text="View > Asset Shelf, Object Mode, mesh selected.", icon='INFO')
        layout.label(text="In the Asset Browser: double-click or right-click a package.")


# (keymap, item) pairs added by register_menus(), removed again by unregister_menus().
_keymap_items = []


def register_menus():
    bpy.types.ASSETBROWSER_MT_context_menu.append(draw_asset_browser_context_menu)
    bpy.types.ASSETSHELF_PT_display.append(draw_shelf_display_settings)
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
    bpy.types.ASSETSHELF_PT_display.remove(draw_shelf_display_settings)
    bpy.types.ASSETBROWSER_MT_context_menu.remove(draw_asset_browser_context_menu)
