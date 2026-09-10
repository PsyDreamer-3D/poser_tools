# SPDX-License-Identifier: GPL-3.0-or-later

# START — workflow remove
_needs_reload = "bpy" in locals()
# END — workflow remove

import bpy

from . import properties, operators, ui

# START — workflow remove
if _needs_reload:
    import sys, importlib

    all_modules = dict(sorted(sys.modules.items(), key=lambda x: x[0]))

    for k, v in all_modules.items():
        if v is not None and (k == __name__ or k.startswith(__name__ + ".")):
            importlib.reload(v)

del _needs_reload
# END — workflow remove

bl_info = {
    "name": "Poser Tools",
    "author": "Jess G",
    "description": "A set of tools to make working with figures imported from Poser easier.",
    "version": (0, 1, 0),
    "blender": (4, 4, 0),
    "location": "View3D",
    "warning": "",
    "category": "Generic",
    "type": "extension"
}


def register():
    properties.register()
    operators.register()
    ui.register()


def unregister():
    ui.unregister()
    operators.unregister()
    properties.unregister()
