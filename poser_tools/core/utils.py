# SPDX-License-Identifier: GPL-3.0-or-later

import bpy

# N-panel tab name shared by every Panel in ui/.
_TAB = "Poser FBX Importer"


def _write_report(context, text_name: str, lines: list[str]) -> None:
    """Write *lines* to a bpy.data.texts block and focus any open Text Editor on it.

    Use this instead of print() for anything a person needs to read — on
    Linux the Blender system console isn't reachable from the UI.
    """
    if text_name in bpy.data.texts:
        tb = bpy.data.texts[text_name]
        tb.clear()
    else:
        tb = bpy.data.texts.new(text_name)
    tb.write("\n".join(lines))
    for area in context.screen.areas:
        if area.type == 'TEXT_EDITOR':
            area.spaces.active.text = tb
            break
