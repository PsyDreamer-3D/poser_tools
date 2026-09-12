# SPDX-License-Identifier: MIT
"""Tests for core/cr2/actor_vertex_index.py.

Covers the local -> global vertex index translation morph injection needs
(docs/handoff-morph-injection.md Phase 2): a targetGeom delta's vertex index
is local to one actor's own geometry, resolved via
`sorted(set(vertex indices that actor's faces touch))` -- the exact
convention cr2_importer's shape_key_importer.py uses.
"""

import textwrap

from core.cr2.actor_vertex_index import load_obj_actor_vertex_groups, resolve_local_index
from core.cr2.cr2_parser import CR2Parser


def _write_obj(tmp_path, text):
    path = tmp_path / "figure.obj"
    path.write_text(textwrap.dedent(text))
    return str(path)


def test_groups_get_sorted_unique_vertex_indices(tmp_path):
    # 6 verts; "chest" touches 0,1,2,3 across two faces (vertex 1 shared by
    # both, appears out of ascending order in the second face on purpose).
    path = _write_obj(tmp_path, """\
        v 0 0 0
        v 1 0 0
        v 1 1 0
        v 0 1 0
        v 2 0 0
        v 2 1 0
        g chest
        f 1 2 3
        f 3 2 5
        g hip
        f 4 5 6
        """)
    groups = load_obj_actor_vertex_groups(path)
    assert groups["chest"] == [0, 1, 2, 4]
    assert groups["hip"] == [3, 4, 5]


def test_faces_before_any_g_directive_land_in_default_group(tmp_path):
    path = _write_obj(tmp_path, """\
        v 0 0 0
        v 1 0 0
        v 1 1 0
        f 1 2 3
        g chest
        f 1 2 3
        """)
    groups = load_obj_actor_vertex_groups(path)
    assert groups["default"] == [0, 1, 2]
    assert groups["chest"] == [0, 1, 2]


def test_g_directive_with_extra_token_uses_first_name_only(tmp_path):
    # cr2_importer's own comment: "Poser sometimes emits 'g groupName' with
    # one name, but can also emit 'g group1 group2'; we take the first name".
    path = _write_obj(tmp_path, """\
        v 0 0 0
        v 1 0 0
        v 1 1 0
        g chest extra_token
        f 1 2 3
        """)
    groups = load_obj_actor_vertex_groups(path)
    assert list(groups.keys()) == ["chest"]


def test_repeated_group_name_accumulates_rather_than_overwriting(tmp_path):
    path = _write_obj(tmp_path, """\
        v 0 0 0
        v 1 0 0
        v 1 1 0
        v 2 0 0
        g chest
        f 1 2 3
        g hip
        f 1 2 4
        g chest
        f 2 3 4
        """)
    groups = load_obj_actor_vertex_groups(path)
    assert groups["chest"] == [0, 1, 2, 3]


def test_negative_relative_face_index_resolves_against_running_vertex_count(tmp_path):
    # "-1" during the 3rd face refers to the vertex just declared (index 2,
    # 0-based) per the OBJ spec's running-count-at-this-point rule.
    path = _write_obj(tmp_path, """\
        v 0 0 0
        v 1 0 0
        v 1 1 0
        g chest
        f 1 2 -1
        """)
    groups = load_obj_actor_vertex_groups(path)
    assert groups["chest"] == [0, 1, 2]


def test_face_refs_with_uv_and_normal_suffixes_use_only_the_vertex_part(tmp_path):
    path = _write_obj(tmp_path, """\
        v 0 0 0
        v 1 0 0
        v 1 1 0
        vt 0 0
        vn 0 0 1
        g chest
        f 1/1/1 2/1/1 3/1/1
        """)
    groups = load_obj_actor_vertex_groups(path)
    assert groups["chest"] == [0, 1, 2]


def test_resolve_local_index_maps_into_the_sorted_group_list():
    group = [4, 17, 92, 200]
    assert resolve_local_index(group, 0) == 4
    assert resolve_local_index(group, 2) == 92


def test_resolve_local_index_out_of_range_returns_none():
    group = [4, 17, 92]
    assert resolve_local_index(group, 3) is None
    assert resolve_local_index(group, -1) is None
    assert resolve_local_index([], 0) is None


def test_real_figure_groups_cover_every_vertex_with_expected_overlap(asset_path):
    # blAiko3.obj: 55 actor groups sum to 74826 vertex references (with
    # shared-seam duplication across adjacent actors) -- matching Aiko3.fbx's
    # own raw (pre remove_loose_verts()) import count exactly. Not a
    # coincidence: those seam duplicates are why the raw FBX vertex count
    # exceeds the OBJ's 72712 unique positions in the first place.
    path = asset_path("blAiko3.obj")
    groups = load_obj_actor_vertex_groups(str(path))
    assert len(groups) == 55
    assert sum(len(v) for v in groups.values()) == 74826
    assert "chest" in groups
    assert groups["chest"] == sorted(groups["chest"])


def test_every_real_cr2_actor_resolves_to_an_obj_group(asset_path):
    # The actual Phase 2 composition point: a CR2 actor's OBJ group name is
    # `actor.geom_name or actor.name` (cr2_parser.Actor) -- confirm that
    # convention holds against real data, not just the OBJ side in isolation.
    cr2_path = asset_path("!Aiko 3.cr2")
    obj_path = asset_path("blAiko3.obj")
    figure = CR2Parser.parse_file(str(cr2_path))
    groups = load_obj_actor_vertex_groups(str(obj_path))

    missing = [
        actor.name for actor in figure.actors
        if (actor.geom_name or actor.name) not in groups
    ]
    # BODY is Poser's whole-figure root actor -- it has no geometry group of
    # its own, so it's the one expected miss, not a bug.
    assert missing == ["BODY"]
