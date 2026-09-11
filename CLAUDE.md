# Poser Tools

Blender 4.4+ add-on (extension, `id = "poser_tools"`, GPL-3.0-or-later). All source lives in `poser_tools/`.

House conventions — no `bpy.ops` for data mutation, bmesh vertex-first deletion, diagnostics via
`bpy.data.texts` instead of `print()`, the `core/`/`operators/`/`properties/`/`ui/` split with
per-subpackage `register()`/`unregister()`, hot-reload markers, manifest-driven versioning — come
from the `blender-addon-scaffold` skill. There is no workspace-root `AGENTS.md` yet for the wider
PsyDreamer-3D set of add-ons; this file plus the skill are the guidance until one exists.

## Layout

Standard scaffold layout. Two things worth knowing:

- Legacy `bl_info` in `__init__.py` coexists with `blender_manifest.toml`. It isn't load-bearing —
  don't remove it, don't add to it beyond what `scripts/bump_version.py` already touches.
- `vendor/io_scene_fbx/` is a vendored copy of Blender's legacy FBX importer (Blender Foundation,
  GPL-2.0-or-later), kept because Poser figures need axis/bone handling the current importer dropped.
  Left as-is with its own license headers; excluded from convention passes.

## Key design decisions

- **Shape-key consolidation is a heuristic reconstruction, not authoritative.** `core/functionsShapeKeys.py`
  groups Poser's split parent/child morphs (full-body morph + per-body-part deltas) by matching
  Blender's `.001`/`.002`-style dedup suffixes (`_TRAILING_DIGITS_RE`) or, for legacy Daz3D M3/M4
  figures, a `p`-prefix (`is_daz=True`). This is necessarily a guess: FBX export bakes shape-key
  geometry but discards Poser's authored channel relationships (ERC / `valueOpDeltaAdd` links), so
  there's no ground-truth grouping available from the FBX alone. Cross-referencing the source `.cr2`
  for canonical names was evaluated and **ruled out** (`docs/handoff-shapekey-improvements.md`
  Phase 5): the CR2 a user has is usually the base figure, and the morphs came from external
  `.pmd`/`.pz2` injections that aren't in it — e.g. `Legacy-Aiko3.cr2` has 18 of the FBX's 638
  shape-key channels. The heuristic is as good as FBX-derived data allows.
- **Legacy Daz3D mode (`is_daz`) is a distinct code path**, not a variant of the same regex — M3/M4
  figures use a `p`/`PBM` prefix convention instead of Blender's numeric-suffix convention, and both
  can theoretically co-occur, so `is_child_shapekey()` checks both independently.
- **Orphan promotion**: a numbered child with no un-numbered parent present gets promoted to a
  standalone parent (`is_promoted_orphan`) and renamed post-consolidation to strip the child-marker
  prefix, so it reads as an ordinary full-body morph rather than disappearing or erroring.
- **A few importer steps stay on `bpy.ops`** — `object.mode_set` (edit-bone access has no data-API
  equivalent), `armature.separate` (no way to split an armature via `bpy.data`), and one
  `object.transform_apply` on the fresh multi-object import selection. Each is commented in place.
  Everything else that mutates data uses `bpy.data`/`bmesh` directly.
- **Shape-key consolidation diagnostics** go to the `"Poser Shapekey Report"` `bpy.data.texts`
  block (full itemized log) plus a one-line `self.report(…)` summary — never `print()`.
  `consolidate_poser_shapekeys()` returns a summary dict carrying that `log`.
- **Child-delta accumulation is an unconditional sum** and stays that way — Poser splits a
  full-body morph into per-actor children with disjoint vertex sets (verified across every test
  figure). `_detect_child_overlap()` is a tripwire only: if a malformed export ever has two
  children moving one vertex, it's reported (`overlaps` key, `{'WARNING'}`), not silently doubled.
- **Consolidation writes a merge record** to `mesh["poser_shapekey_merges"]` (JSON: `version`,
  `merges` keyed by final shape-key name → `{children, promoted_orphan}`, `renamed`). It's for a
  future pass to resolve a pre-merge morph name back to the surviving key. `mesh`, not `ShapeKey` —
  the latter has no ID properties. `obj["morphs_consolidated"]` stays as the re-run guard.
- **JCM morphs are deleted during consolidation** — `is_jcm_shapekey()` (`name.startswith('JCM')`,
  deliberately not a substring test — LaFemme ships a `"ON <- Use JCM -> OFF"` control morph).
  `consolidate_poser_shapekeys()` removes them up front. FBX export bakes the corrective *shape*
  but discards the bone-rotation ERC link that drives it, so the key can never fire — keeping it
  is keeping dead weight. Poser's export uses both `"JCM Foo Bar"` and `"JCMrFooBar"` forms.

## Out of scope (permanent)

Weight-mapped / TriAx rigging support. Ruled out after real evaluation — the Poser/DAZ ecosystem
already solved it and reimplementing it worse isn't worth the scope. Don't resurrect it here or in
`cr2_importer`.

## Shape-key consolidation history

`docs/handoff-shapekey-improvements.md` — a 5-phase pass over the consolidation flow, all resolved:
diagnostics (report text block), JCM deletion, overlap detection (tripwire), merge metadata on
`obj.data`, and a CR2 cross-reference that was spiked and ruled out. Read it before touching
`core/functionsShapeKeys.py` — it records why each piece is shaped the way it is.

## Testing

No automated test harness (`tests/` absent) — manual smoke test only:

1. Install via Blender Preferences → Extensions → Install from Disk, pointing at `poser_tools/` (or
   drag-and-drop a built zip). The add-on should enable with no registration errors.
2. Import a Poser-exported FBX via the **Poser FBX Importer** N-panel tab → **Import Poser FBX**.
   For an M3/M4 figure tick **Legacy Daz3D Figure** in the import dialog. Check: mesh + armature
   import, material-slot order restored, loose verts gone, bone rolls sane, neck centered,
   conforming figures split into their own armatures. Shape keys: parent/child morphs collapsed
   into single sliders, no `.001`-suffixed or `JCM…` keys left, a **Poser Shapekey Report** text
   block written, `mesh["poser_shapekey_merges"]` set.
3. Re-run check: **Fix Poser Shapekeys** on the same mesh → "already consolidated" info, no-op
   (`obj["morphs_consolidated"]`). Untick **Consolidate Shape Keys** in a fresh import → raw shape
   keys, no report, then the button does the work.
4. Armature selected → **Rename Armature Bones** adds `.L`/`.R` suffixes. Mesh selected →
   **Rename Weight Groups** applies the matching rename to vertex groups.

For a quick check without Blender: `python -m py_compile` the tree, or import `poser_tools` under a
stubbed `bpy` and call `register()`/`unregister()` — that catches class-tuple typos and bad import
paths, though not runtime behavior.
