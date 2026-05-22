import re
import bpy
import numpy as np

# we need to check for existing value, and if it's there, it should be saved
# Older Daz and other Poser figures append "p" to morphs on body parts that are
# related to the fbm. If the morph has a lower-case p, it should be treated the same as if it had a number suffix

_TRAILING_DIGITS_RE = re.compile(r'\.[0-9]{3}')


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


def build_parent_shapekey_list(shapekeys, _is_daz=False):
    _fbm_shape_keys = {}
    for sh in shapekeys:
        sh_name = sh.name
        if sh_name == "Basis":
            continue

        if not is_child_shapekey(sh_name, _is_daz):
            if sh_name[0] == 'p':
                print(sh_name, 'is not a child shapekey')
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


def build_fbm_shapekey_list(shapekeys, _is_daz=False):
    parent_shapekeys = build_parent_shapekey_list(shapekeys, _is_daz)
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

    return fbms


def is_child_shapekey(sh_name, _is_daz=False):
    has_p = sh_name[0] == 'p' and _is_daz
    has_trailing_digits = _TRAILING_DIGITS_RE.search(sh_name) is not None

    if has_p and not has_trailing_digits:
        return True

    if has_p and has_trailing_digits:
        return True

    if has_trailing_digits:
        return True

    return False


def get_parent_name(sh_name, _is_daz=False):
    has_p = sh_name[0] == 'p' and _is_daz
    has_trailing_digits = _TRAILING_DIGITS_RE.search(sh_name) is not None

    if has_p and not has_trailing_digits:
        return sh_name[1:]  # return the name sans prefix

    if has_p and has_trailing_digits:
        return _TRAILING_DIGITS_RE.sub('', sh_name[1:])

    if not has_p and has_trailing_digits:
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
    fbm_shapekeys = build_fbm_shapekey_list(shapekeys, _is_daz)
    mute_all_shapekeys(shapekeys)

    n = len(obj.data.vertices) * 3
    basis_co = np.empty(n, dtype=np.float32)
    shapekeys["Basis"].data.foreach_get("co", basis_co)

    # Pre-read all child coords before any deletions
    all_child_coords = {}
    for morph_data in fbm_shapekeys.values():
        for child_name in morph_data['children']:
            buf = np.empty(n, dtype=np.float32)
            shapekeys[child_name].data.foreach_get("co", buf)
            all_child_coords[child_name] = buf

    print('\nConverting Shapekeys...')
    shapekeys_processed = []

    for morph in fbm_shapekeys:
        has_children = len(fbm_shapekeys[morph]['children']) > 0
        fbm_empty = is_shapekey_empty(morph, shapekeys, basis_co)

        if not has_children and fbm_empty:
            print('---', morph, 'is empty and has no children...skipping...')
            shapekeys[morph].mute = True
            continue

        if not has_children and not fbm_empty:
            print('---', morph, 'is a working shapekey...skipping...')
            shapekeys_processed.append(morph)
            continue

        # has_children is True from here
        child_coords = {ch: all_child_coords[ch] for ch in fbm_shapekeys[morph]['children']}
        accumulate_fbm_shapekey(fbm_shapekeys, morph, shapekeys, basis_co, child_coords, not fbm_empty)

        print('.......', morph, 'converted!')
        shapekeys_processed.append(morph)

        for child_key in fbm_shapekeys[morph]['children']:
            print('--- deleting child shapekey', child_key)
            remove_shapekey(obj, shapekeys[child_key])
        print(' ')

    for morph in shapekeys_processed:
        shapekeys[morph].mute = False
