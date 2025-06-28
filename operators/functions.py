import re
import bpy
import numpy as np
import queue


# we need to check for existing value, and if it's there, it should be saved
# Older Daz and other Poser figures append "p" to morphs on body parts that are
# related to the fbm. If the morph has a lower-case p, it should be treated the same as if it had a number suffix

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


def build_parent_shapekey_list(shapekeys):
    _fbm_shape_keys = {}
    for sh in shapekeys:
        sh_name = sh.name
        if sh_name == "Basis":
            continue

        if not is_child_shapekey(sh_name):
            _fbm_shape_keys[sh_name] = sh.value

    return _fbm_shape_keys


def build_child_shapekey_list(shapekeys):
    _fbm_shape_keys = {}

    for sh in shapekeys:
        sh_name = sh.name
        if not is_child_shapekey(sh_name):
            continue

        _fbm_shape_keys[sh_name] = sh.value

    return _fbm_shape_keys


def build_fbm_shapekey_list(shapekeys):
    parent_shapekeys = build_parent_shapekey_list(shapekeys)
    child_shapekeys = build_child_shapekey_list(shapekeys)

    fbms = {}
    for fbm in parent_shapekeys:
        fbms[fbm] = {
            "value": parent_shapekeys[fbm],
            "children": {}
        }

        for ch in child_shapekeys:
            if fbm != get_parent_name(ch):
                continue

            fbms[fbm]["children"][ch] = child_shapekeys[ch]

    return fbms


def is_child_shapekey(sh_name):
    global is_daz
    if re.search(r'\.[0-9]{3}', sh_name) is not None:
        return True

    if sh_name[0] == 'p' and is_daz:
        return True

    return False


def get_parent_name(sh_name):
    has_p = sh_name[0] == 'p' and is_daz
    has_trailing_digits = re.search(r'\.[0-9]{3}', sh_name)

    if has_p and not has_trailing_digits:
        return sh_name[1:]  # return the name sans prefix

    if has_p and has_trailing_digits:
        return re.sub(r'\.[0-9]{3}', '', sh_name[1:])

    if not has_p and has_trailing_digits:
        return re.sub(r'\.[0-9]{3}', '', sh_name)


def delete_shapekey(obj, sh_name, shapekeys):
    set_active_shapekey(obj, sh_name, shapekeys)
    bpy.ops.object.shape_key_remove()


def set_active_shapekey(obj, sh_name, shapekeys):
    index = shapekeys.keys().index(sh_name)
    obj.active_shape_key_index = index


def move_active_shapekey_to_bottom(obj, sh_name, shapekeys):
    set_active_shapekey(obj, sh_name, shapekeys)
    bpy.ops.object.shape_key_move(type="BOTTOM")


def is_shapekey_empty(sh_name, shapekeys):
    # compare with Basis
    normals_basis = shapekeys["Basis"].normals_vertex_get()
    normals_selected_shapekey = shapekeys[sh_name].normals_vertex_get()

    return normals_basis == normals_selected_shapekey


def create_fbm_shapekey(master_shapekeys, morph, obj, shapekeys):
    tmp_morph_name = 'TMP_' + morph
    # create new fbm
    obj.shape_key_add(name=tmp_morph_name, from_mix=True)

    # set min and max values to Poser defaults
    shapekeys[morph].slider_max = 10.0
    shapekeys[morph].slider_min = -10.0

    # set newly created fbm back to its original value
    shapekeys[morph].value = master_shapekeys[morph]['value']
    shapekeys[morph].mute = True  # mute new shapekey

    move_shape_key(obj, tmp_morph_name, morph)
    delete_shapekey(obj, tmp_morph_name, shapekeys)


def move_shape_key(obj, key_name, key_name_to_swap):
    me = obj.data
    verts = me.vertices
    c = len(verts)

    key1 = me.shape_keys.key_blocks[key_name]
    key2 = me.shape_keys.key_blocks[key_name_to_swap]

    key1_data = np.zeros(c * 3, dtype=np.float32)
    key1.data.foreach_get("co", key1_data.ravel())

    key2_data = np.zeros(c * 3, dtype=np.float32)
    key2.data.foreach_get("co", key2_data.ravel())

    key1.data.foreach_set("co", key2_data)
    key2.data.foreach_set("co", key1_data)


def activate_child_shapekeys(fbm_shapekeys, morph, shapekeys):
    for child_shapekey in fbm_shapekeys[morph]['children']:
        shapekeys[child_shapekey].mute = False
        shapekeys[child_shapekey].value = 1


def mute_child_shapekeys(fbm_shapekeys, morph, shapekeys):
    for child_morph in fbm_shapekeys[morph]['children']:
        shapekeys[child_morph].mute = True
        shapekeys[child_morph].value = 0


def consolidate_poser_shapekeys(obj, shapekeys):
    fbm_shapekeys = build_fbm_shapekey_list(shapekeys)
    mute_all_shapekeys(shapekeys)
    print(' ')
    print('Converting Shapekeys...')
    shapekeys_processed = []
    for morph in fbm_shapekeys:
        if morph.find('JCM') != -1:  # skip JCMs, we don't need these in Blender
            print(morph, 'is a JCM. Skip for deletion later')
            shapekeys[morph].mute = True
            continue

        if len(fbm_shapekeys[morph]['children']) == 0 and is_shapekey_empty(morph, shapekeys):
            print('---', morph, 'is empty and has no children...skipping...')
            shapekeys[morph].mute = True
            continue

        # if there's no children and the shapekey isn't empty, continue on as this is a working shapekey
        if len(fbm_shapekeys[morph]['children']) == 0 and not is_shapekey_empty(morph, shapekeys):
            print('--- ', morph, 'is a working shapekey...skipping...')
            shapekeys_processed.append(morph)
            continue

        # we have a situation where the fbm shapekey isn't empty AND has children. We need a different approach
        if len(fbm_shapekeys[morph]['children']) >= 1 and not is_shapekey_empty(morph, shapekeys):
            print('--- ', morph, 'is a working shapekey but has children...processing...')
            # we know that the existing fbm has children but isn't empty
            # we turn all them on, create a new shapekey like normal, but give it a slightly different name
            # turn on existing shape key
            shapekeys[morph].value = 1
            shapekeys[morph].mute = False

        activate_child_shapekeys(fbm_shapekeys, morph, shapekeys)
        create_fbm_shapekey(fbm_shapekeys, morph, obj, shapekeys)
        mute_child_shapekeys(fbm_shapekeys, morph, shapekeys)

        print('....... ', morph, ' converted!')
        shapekeys_processed.append(morph)

        # delete all child keys
        for child_key in fbm_shapekeys[morph]['children']:
            print('--- deleting child shapekey', child_key)
            delete_shapekey(obj, child_key, shapekeys)
        print(' ')

    # unmute our master keys
    for morph in shapekeys_processed:
        shapekeys[morph].mute = False

# print(build_fbm_shapekey_list(shapekeys))
# consolidate_poser_shapekeys(obj, shapekeys)
