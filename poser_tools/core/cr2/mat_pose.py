# SPDX-License-Identifier: GPL-3.0-or-later
"""
mat_pose.py
-----------
Phase 8 (docs/handoff-morph-injection.md): reading a Poser MAT pose and matching its
materials to the material slots of an FBX-imported figure. No bpy.

A MAT pose is `material` blocks and nothing else (owner's rule). CR2Parser already reads
them, at figure level or per actor, legacy `KdColor`/`textureMap` or shader trees, so this
module only adds the matching.

The FBX import names each slot `<object>_<material>` (Aiko3 `Figure_SkinTorso`, LaFemme
`LaFemme_Pro_SkinTorso`; measured in the Phase 8 spike), so a MAT's `SkinTorso` never
matches a slot as written. Matching strips that prefix and Blender's `.001` duplicate
suffix, then compares names exactly. Whatever doesn't match is reported, never guessed:
a V3 MAT's `SkinLeg` has no slot on Aiko3, whose CR2 splits the leg into three materials.
"""

import os
import re

from .cr2_parser import CR2Parser
from .poser_io import read_poser_file

# Poser's own viewport stand-in material: in every CR2 and most MATs, never on an exported mesh.
PREVIEW_MATERIAL = "Preview"
_DUPLICATE_SUFFIX_RE = re.compile(r"\.\d{3}$")


def read_mat_pose(path: str) -> dict:
    """{material name: PoserMaterial} for a MAT pose file, in file order."""
    return CR2Parser.parse_text(read_poser_file(path), source_file=path).materials


def slot_prefix(slot_names, object_name: str = "") -> str:
    """The `<object>_` prefix the FBX import put on every slot name, or "".

    The object's own name first (what the import used); if the object was renamed since,
    the longest prefix ending in `_` that every slot shares and that leaves each a name.
    """
    names = [_DUPLICATE_SUFFIX_RE.sub("", n) for n in slot_names if n]
    if not names:
        return ""
    if object_name and all(n.startswith(object_name + "_") and len(n) > len(object_name) + 1 for n in names):
        return object_name + "_"
    common = os.path.commonprefix(names)
    cut = common.rfind("_")
    if cut < 0:
        return ""
    prefix = common[:cut + 1]
    return prefix if all(len(n) > len(prefix) for n in names) else ""


def match_materials_to_slots(material_names, slot_names, object_name: str = "") -> dict:
    """Pair a MAT's materials with the mesh's slot materials.

    Returns {"pairs": [(material name, slot material name)] in MAT order,
             "no_slot": [MAT materials with no slot on this mesh, Preview left out],
             "untouched": [slot materials the MAT doesn't set]}.
    Exact names after the prefix and suffix are stripped; case is ignored only when that
    is the sole difference, since Poser's names are case-sensitive but vendors aren't.
    """
    slots = [n for n in dict.fromkeys(slot_names) if n]
    prefix = slot_prefix(slots, object_name)
    bare = {}
    for slot in slots:
        key = _DUPLICATE_SUFFIX_RE.sub("", slot)[len(prefix):]
        bare.setdefault(key, slot)
    folded = {}
    for key, slot in bare.items():
        folded.setdefault(key.casefold(), slot)

    pairs, no_slot, used = [], [], set()
    for name in material_names:
        slot = bare.get(name) or folded.get(name.casefold())
        if slot is None:
            if name != PREVIEW_MATERIAL:
                no_slot.append(name)
        elif slot not in used:
            pairs.append((name, slot))
            used.add(slot)
    untouched = [slot for slot in slots if slot not in used]
    return {"pairs": pairs, "no_slot": no_slot, "untouched": untouched}
