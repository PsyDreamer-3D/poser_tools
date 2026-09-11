# SPDX-License-Identifier: GPL-3.0-or-later

import re
from collections import defaultdict
import bpy
import numpy as np

# we need to check for existing value, and if it's there, it should be saved
# Older Daz and other Poser figures append "p" to morphs on body parts that are
# related to the fbm. If the morph has a lower-case p, it should be treated the same as if it had a number suffix

_TRAILING_DIGITS_RE = re.compile(r'\.[0-9]{3}')
_PBM_RE = re.compile(r'^PBM')


def mute_all_shapekeys(shapekeys):
    for sh in shapekeys:
        sh_name = sh.name
        if sh_name == "Basis":
            continue

        if sh.mute is True:
            continue

        sh.mute = True


def unmute_all_shapekeys(shapekeys):
    for sh in shapekeys:
        sh_name = sh.name
        if sh_name == "Basis":
            continue

        if sh.mute is False:
            continue

        sh.mute = False


def build_parent_shapekey_list(shapekeys, _is_daz=False, log=None):
    _fbm_shape_keys = {}
    for sh in shapekeys:
        sh_name = sh.name
        if sh_name == "Basis":
            continue

        if not is_child_shapekey(sh_name, _is_daz):
            if log is not None and sh_name[:1] == 'p':
                log.append(f'note: "{sh_name}" is p-prefixed but treated as a full-body morph')
            _fbm_shape_keys[sh_name] = sh.value

    return _fbm_shape_keys


def build_child_shapekey_list(shapekeys, _is_daz=False):
    _fbm_shape_keys = {}

    for sh in shapekeys:
        sh_name = sh.name
        if not is_child_shapekey(sh_name, _is_daz):
            continue

        _fbm_shape_keys[sh_name] = sh.value

    return _fbm_shape_keys


def build_fbm_shapekey_list(shapekeys, _is_daz=False, log=None):
    parent_shapekeys = build_parent_shapekey_list(shapekeys, _is_daz, log)
    child_shapekeys = build_child_shapekey_list(shapekeys, _is_daz)

    fbms = {}
    for fbm in parent_shapekeys:
        fbms[fbm] = {
            "value": parent_shapekeys[fbm],
            "children": {}
        }

        for ch in child_shapekeys:
            if fbm != get_parent_name(ch, _is_daz):
                continue

            fbms[fbm]["children"][ch] = child_shapekeys[ch]

    # Promote orphaned children (no matching parent) to standalone parents.
    # Group siblings by their shared missing parent name so that the un-numbered
    # variant (e.g. pPregnant) becomes the promoted parent and its numbered
    # siblings (pPregnant.001, pPregnant.002, …) become its children —
    # matching the normal consolidation flow.
    orphan_groups = defaultdict(list)
    for ch in child_shapekeys:
        parent_name = get_parent_name(ch, _is_daz)
        if parent_name not in fbms:
            orphan_groups[parent_name].append(ch)

    for missing_parent, orphans in orphan_groups.items():
        base = [o for o in orphans if not _TRAILING_DIGITS_RE.search(o)]
        numbered = [o for o in orphans if _TRAILING_DIGITS_RE.search(o)]

        if base:
            promoted = base[0]
            # Any extra base morphs (shouldn't happen) fall back to numbered.
            extra = base[1:]
            children = extra + numbered
            if log is not None:
                log.append(
                    f'warning: no full-body morph for "{missing_parent}" — '
                    f'promoting "{promoted}" as standalone with {len(children)} child(ren)'
                )
            fbms[promoted] = {
                "value": child_shapekeys[promoted],
                "children": {ch: child_shapekeys[ch] for ch in children},
                "is_promoted_orphan": True,
            }
        else:
            # All orphans are numbered; promote each independently.
            for orphan in numbered:
                if log is not None:
                    log.append(
                        f'warning: no full-body morph for "{missing_parent}" — '
                        f'promoting "{orphan}" as standalone'
                    )
                fbms[orphan] = {"value": child_shapekeys[orphan], "children": {}}

    return fbms


def is_jcm_shapekey(sh_name):
    """True for a joint-corrective morph — deleted during consolidation, not merged.

    Poser's FBX export names these either spaced ("JCM Left Knee Bend 90") or
    camelCase ("JCMrElbowBend130"); Blender's dedup can add a .NNN suffix. The
    check is `startswith`, not a substring — LaFemme ships a control morph
    literally named "ON <- Use JCM -> OFF" that a substring test would eat.
    Verified against LaFemme / Aiko3 / Kira FBX exports.
    """
    return sh_name.startswith('JCM')


def is_child_shapekey(sh_name, _is_daz=False):
    if _is_daz and (sh_name[0] == 'p' or _PBM_RE.match(sh_name)):
        return True
    if _TRAILING_DIGITS_RE.search(sh_name):
        return True
    return False


def get_parent_name(sh_name, _is_daz=False):
    has_trailing_digits = _TRAILING_DIGITS_RE.search(sh_name) is not None

    if _is_daz and _PBM_RE.match(sh_name):
        base = _PBM_RE.sub('', sh_name)
        return _TRAILING_DIGITS_RE.sub('', base)

    if _is_daz and sh_name[0] == 'p':
        return _TRAILING_DIGITS_RE.sub('', sh_name[1:])

    if has_trailing_digits:
        return _TRAILING_DIGITS_RE.sub('', sh_name)


def remove_shapekey(obj, key_block):
    obj.shape_key_remove(key_block)


def is_shapekey_empty(sh_name, shapekeys, basis_co):
    key_co = np.empty(len(basis_co), dtype=np.float32)
    shapekeys[sh_name].data.foreach_get("co", key_co)
    return np.array_equal(basis_co, key_co)


def accumulate_fbm_shapekey(master_shapekeys, morph, shapekeys, basis_co, child_coords, fbm_has_data):
    fbm_key = shapekeys[morph]
    n = len(basis_co)

    if fbm_has_data:
        # Start from FBM's own coords, then add each child's displacement on top
        result_co = np.empty(n, dtype=np.float32)
        fbm_key.data.foreach_get("co", result_co)
        # result = FBM_co + sum(child_co - basis_co)
    else:
        result_co = basis_co.copy()
        # result = basis_co + sum(child_co - basis_co)

    for child_co in child_coords.values():
        result_co += child_co - basis_co

    fbm_key.data.foreach_set("co", result_co)
    fbm_key.slider_max = 1.0
    fbm_key.slider_min = -1.0
    fbm_key.value = master_shapekeys[morph]['value']
    fbm_key.mute = True


def consolidate_poser_shapekeys(obj, shapekeys, _is_daz=False):
    """Merge Poser's split parent/child morphs into single shape keys.

    Returns a summary dict:
        consolidated      – FBM names that absorbed one or more children
        working_kept      – childless non-empty keys left untouched
        empty_skipped     – childless empty keys that were muted
        promoted          – orphan children promoted to standalone parents
        renamed           – {old_name: new_name} for promoted-orphan prefix strips
        children_deleted  – child key names removed after merging
        jcm_removed       – JCM (joint-corrective) key names deleted from the mesh
        log               – full itemized text, one entry per line, for _write_report()
    """
    log = []
    result = {
        "consolidated": [],
        "working_kept": [],
        "empty_skipped": [],
        "promoted": [],
        "renamed": {},
        "children_deleted": [],
        "jcm_removed": [],
        "log": log,
    }

    # JCM morphs are pose-driven joint correctives. FBX export bakes the shape
    # but discards the ERC/driver relationship that fires it, so the key can
    # never work as intended — it's just dead weight. Delete them up front
    # (cr2_importer, working from the .cr2, never creates them at all).
    jcm_names = [sh.name for sh in shapekeys if is_jcm_shapekey(sh.name)]
    result["jcm_removed"] = jcm_names
    if jcm_names:
        log.append(f"Removed {len(jcm_names)} JCM morph(s):")
        for name in jcm_names:
            log.append(f"  {name}")
        for name in jcm_names:
            obj.shape_key_remove(shapekeys[name])

    fbm_shapekeys = build_fbm_shapekey_list(shapekeys, _is_daz, log)
    result["promoted"] = [m for m, d in fbm_shapekeys.items() if d.get("is_promoted_orphan")]

    mute_all_shapekeys(shapekeys)

    n = len(obj.data.vertices) * 3
    basis_co = np.empty(n, dtype=np.float32)
    shapekeys["Basis"].data.foreach_get("co", basis_co)

    # Build a flat list of child names so we know the total work upfront.
    all_child_names = [
        ch
        for morph_data in fbm_shapekeys.values()
        for ch in morph_data['children']
    ]
    total_steps = len(all_child_names) + len(fbm_shapekeys)

    wm = bpy.context.window_manager
    wm.progress_begin(0, max(total_steps, 1))
    step = 0

    try:
        # Pre-read all child coords before any deletions.
        all_child_coords = {}
        for child_name in all_child_names:
            buf = np.empty(n, dtype=np.float32)
            shapekeys[child_name].data.foreach_get("co", buf)
            all_child_coords[child_name] = buf
            step += 1
            wm.progress_update(step)

        log.append("Converting shape keys...")
        shapekeys_processed = []

        for morph in fbm_shapekeys:
            has_children = len(fbm_shapekeys[morph]['children']) > 0
            fbm_empty = is_shapekey_empty(morph, shapekeys, basis_co)

            if not has_children and fbm_empty:
                log.append(f'  {morph}: empty and childless — muted')
                result["empty_skipped"].append(morph)
                shapekeys[morph].mute = True
            elif not has_children and not fbm_empty:
                log.append(f'  {morph}: working shape key — kept as-is')
                result["working_kept"].append(morph)
                shapekeys_processed.append(morph)
            else:
                # has_children is True from here
                child_coords = {ch: all_child_coords[ch] for ch in fbm_shapekeys[morph]['children']}
                accumulate_fbm_shapekey(fbm_shapekeys, morph, shapekeys, basis_co, child_coords, not fbm_empty)

                child_names = list(fbm_shapekeys[morph]['children'])
                log.append(f'  {morph}: converted ({len(child_names)} child(ren) merged)')
                result["consolidated"].append(morph)
                shapekeys_processed.append(morph)

                for child_key in child_names:
                    log.append(f'      deleted child shape key {child_key}')
                    result["children_deleted"].append(child_key)
                    remove_shapekey(obj, shapekeys[child_key])

            step += 1
            wm.progress_update(step)

        for morph in shapekeys_processed:
            shapekeys[morph].mute = False

        # Rename promoted orphan morphs: strip the child prefix so they read as
        # full-body morphs (e.g. pPregnant → Pregnant).
        for morph in shapekeys_processed:
            if not fbm_shapekeys[morph].get('is_promoted_orphan'):
                continue
            new_name = get_parent_name(morph, _is_daz)
            if new_name and new_name not in shapekeys:
                log.append(f'  renamed "{morph}" → "{new_name}"')
                result["renamed"][morph] = new_name
                shapekeys[morph].name = new_name

    finally:
        wm.progress_end()

    return result
