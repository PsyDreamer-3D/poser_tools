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
- `core/cr2/` (`cr2_parser.py`, `constants.py`, `poser_io.py`, `name_match.py`,
  `actor_vertex_index.py`, `poser_paths.py`) is **not** `vendor/` — it's adapted from `cr2_importer`
  (PsyDreamer-3D's own now-unmaintained CR2 importer) but actively maintained here going forward,
  per Phase 5 of the handoff doc. Files keep their original MIT SPDX header (same exception
  `vendor/io_scene_fbx` sets), but this code gets fixed and extended in place, not frozen. No `bpy`
  dependency — pure Python, covered by `tests/test_cr2_parser.py`, `tests/test_actor_vertex_index.py`,
  and `tests/test_poser_paths.py`.
  `core/cr2/obj_io.py`, `core/cr2/mesh_correspondence.py`, `core/cr2/injection_package.py`, and
  `core/cr2/apply_injection.py` (morph-injection Phases 1 and 3, see below) live in the same package
  but are original `poser_tools` code, not adapted from `cr2_importer` — GPL-3.0-or-later, not MIT.
  Same no-`bpy` / plain-`pytest` discipline (`tests/test_mesh_correspondence.py`,
  `tests/test_injection_package.py`, `tests/test_apply_injection.py`).
  Which license a new `core/cr2/` file gets is a judgment call, not automatic just because it's in
  this package — MIT when it's a real adaptation of specific `cr2_importer` logic (like
  `actor_vertex_index.py`'s local-index convention, or `poser_paths.py`'s `PoserPathResolver`,
  confirmed by reading `cr2_importer`'s own code), GPL when it's original code solving a problem
  `cr2_importer` never had (like `mesh_correspondence.py`, needed only because this add-on injects
  into an already-imported mesh rather than building one from scratch, or `injection_package.py`'s
  `readScript`-following — `cr2_importer`'s own `pz2_parser.py` only *detects* that an orchestrator
  file exists, it never actually follows one).

## Key design decisions

- **Shape-key consolidation is a heuristic reconstruction, not authoritative.** `core/functionsShapeKeys.py`
  groups Poser's split parent/child morphs (full-body morph + per-body-part deltas) by matching
  Blender's `.001`/`.002`-style dedup suffixes (`_TRAILING_DIGITS_RE`) or, for legacy Daz3D M3/M4
  figures, a `p`-prefix (`is_daz=True`). This is necessarily a guess: FBX export bakes shape-key
  geometry but discards Poser's authored channel relationships (ERC / `valueOpDeltaAdd` links), so
  there's no ground-truth grouping available from the FBX alone. Cross-referencing the source `.cr2`
  for canonical names is **under active re-evaluation** (`docs/handoff-shapekey-improvements.md`
  Phase 5, reopened) — a first spike ruled it out against an incomplete test CR2, but a proper
  base-figure CR2 gets 100% naming coverage after fixing a real bug in `core/cr2/cr2_parser.py`
  (adapted from the now-unmaintained `cr2_importer`). `core/cr2/name_match.py` resolves a morph
  name to its full per-actor `Channel` group (a name is rarely one channel — Poser splits a
  body-wide morph across every actor it touches). The heuristic below stays authoritative
  until/unless Phase 5 lands a design for actually using this in the add-on.
- **Legacy Daz3D mode (`is_daz`) is a distinct code path**, not a variant of the same regex — M3/M4
  figures use a `p`/`PBM` prefix convention instead of Blender's numeric-suffix convention, and both
  can theoretically co-occur, so `is_child_shapekey()` checks both independently.
- **Orphan promotion**: a child with no matching top-level parent present gets promoted to a
  standalone parent (`is_promoted_orphan`) and renamed post-consolidation to strip the child-marker
  prefix, so it reads as an ordinary full-body morph rather than disappearing or erroring. Applies
  equally to a `.NNN`-numbered orphan, a bare `p`-prefixed one with no digits at all, and a
  `PBM`-prefixed one (seen on injected Stephanie 3 morphs) — `get_parent_name()` is prefix-aware for
  both `p` and `PBM`, and the promotion step only cares about the computed missing-parent name, not
  which convention produced it. Verified for both prefixes and the no-top-level case with a scratch
  test (not committed — `tests/` doesn't exist yet).
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

`docs/handoff-shapekey-improvements.md` — a 5-phase pass over the consolidation flow. Phases 1–4
shipped: diagnostics (report text block), JCM deletion, overlap detection (tripwire), merge
metadata on `obj.data`. Phase 5 (CR2 cross-reference) is reopened — see the doc for the current
naming-correspondence numbers and the `cr2_importer` parser bug write-up. Read the doc before touching
`core/functionsShapeKeys.py` — it records why each piece is shaped the way it is.

## Morph injection

`docs/handoff-morph-injection.md` — a separate, bigger capability than consolidation: importing a
3rd-party Poser morph package (`.pz2` injection template) and adding its morphs as *new* shape keys
to an already-imported mesh, not just grouping ones the FBX export already baked. Builds on
`core/cr2/`. Phases 1-3 and 5 are shipped; Phase 4 (PMD binary reader) is **paused indefinitely** —
don't resume it without a specific reason to.

- Phase 1 (vertex correspondence: `core/cr2/obj_io.py` + `core/cr2/mesh_correspondence.py`) matches
  a Poser-native `.obj` against an FBX-imported mesh by position (not raw index), recovering the
  transform automatically. Its documented 97.7%/68.1% match rate turned out to be measured against
  the *unstripped* raw FBX mesh (scratch scripts bypassed `remove_loose_verts()`) — real usage
  (through `remove_loose_verts()`) measured cleaner in a real Phase 5 end-to-end run; see the doc's
  Phase 5 section for the caveat and why it isn't re-verified at full scale yet.
- Phase 2 (per-actor local-index → OBJ-global-index resolution: `core/cr2/actor_vertex_index.py`)
  is MIT-headed, unlike Phases 1/3's GPL originals — a direct adaptation of `cr2_importer`'s
  group-tracking + `sorted(set(...))` local-index convention.
- Phase 3 (applying an injection: `core/cr2/poser_paths.py`, `core/cr2/injection_package.py`,
  `core/cr2/apply_injection.py`) composes Phases 1+2 into `build_shape_key_positions()` — a plain
  numpy position array, not yet a real `bpy.types.ShapeKey` (that's Phase 5's job, keeping this
  `bpy`-free like the rest of `core/cr2/`).
- Phase 5 (`operators/applyMorphInjection.py` + `ui/applyMorphInjection.py`,
  `poser.apply_morph_injection`) is the actual user-facing operator — a popup collects the
  injection file + reference OBJ (remembered on the mesh as `poser_reference_obj`), applies every
  morph in the package in one run (skips JCM-named and already-present ones), reports to a
  `"Poser Morph Injection Report"` text block. Real end-to-end verified (real import + real
  operator call, not just registration).
- Phase 5.1: the correspondence build no longer blocks Blender's UI while it runs. Real UAT showed
  the original blocking call made Blender look hung (grey window, no progress feedback) well before
  it finished. `execute()` now branches on `bpy.app.background` — interactively, the slow call runs
  on a background `threading.Thread` while a `wm.event_timer_add()`-driven `modal()` polls it and
  updates the status bar with elapsed time (Esc abandons the wait); headless (`--background`, or a
  scripted `bpy.ops` call) stays fully synchronous, since nothing dispatches `TIMER` events there.
  `context.window is None` is *not* a valid headless check — a `Window` datablock exists even under
  `--background --factory-startup` on this Blender build; use `bpy.app.background`.
- Phase 5.2: the correspondence build itself is now ~9x faster (real Aiko3+BrowHeavy:
  295.4s → 31.8s, `touched=2295 unmapped=0` unchanged). `core/cr2/mesh_correspondence.py`'s two real
  full-mesh passes were rewritten from a per-point Python loop to a vectorized, sorted-cell-grid
  search (`_query_grid`/`_build_target_grid`), each pass sized to the actual distance tolerance it
  needs rather than "search everywhere then threshold." `_coarse_align`'s 48-candidate scoring
  search deliberately stayed on the *old* per-point loop — it was never the bottleneck (~13-20s of
  the original 283s), and vectorizing it hit a real trap on real mesh data (a dense anatomical
  region can make a vectorized batch's candidate count explode; a per-point loop pays for that
  region individually and cheaply instead). See `docs/handoff-morph-injection.md`'s Phase 5.1/5.2
  sections for the full reasoning, including the specific tolerance values tuned against real
  Aiko3 density data, not just a synthetic benchmark.
- Phase 5.3: `build_vertex_correspondence()` takes an optional `progress_callback(fraction)`,
  called at its real phase boundaries (coarse alignment, pass 1, pass 2/refit) — genuine progress
  through measured-cost phases, not a synthetic tick. The operator wires this to a plain attribute
  written from the background thread and read by `modal()`'s timer tick on the main thread (no bpy
  calls off the main thread), driving `wm.progress_update()` for a real percentage on the cursor —
  matching **Import Poser FBX**'s own progress cursor — instead of the static 50% the Phase 5.1 fix
  left in place. Newly applied shape keys also now rest at `value = 0.0` — `shape_key_add()`
  defaults to `1.0` (fully dialed in), which stacked badly when a package applies dozens of morphs
  in one run.

## Testing

`tests/` has a real `pytest` suite, so far scoped to `core/cr2/` (pure Python, no `bpy`, exactly
the parsing-logic case that gets real tests rather than manual verification). One-time setup:
`.venv/bin/pip install pytest`; run with `.venv/bin/python -m pytest`. Fixture-backed tests read
real CR2 files from an external `Test_Poser_Assets/` directory (not committed — large binaries,
machine-specific path) and skip cleanly if it isn't present.

Everything else is manual smoke test only — no `bpy`-dependent code has automated coverage:

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
5. Mesh selected → **Apply Morph Injection**, pick a real injection `.pz2` and the figure's
   reference `.obj` in the popup. Expect: new shape keys named after the package's morphs, a
   **Poser Morph Injection Report** text block (including a total-time line), `mesh["poser_reference_obj"]`
   set. Expect roughly 30-60s on a 70k-vertex figure, dominated by the one-time
   vertex-correspondence build — the cursor shows a counting-up percentage (matching **Import
   Poser FBX**'s own progress cursor) and the status bar shows both the percentage and elapsed
   seconds the whole time, window never greys out, Esc cancels cleanly. Re-run with the same
   package → reports "already present", no duplicate keys, same ~30-60s cost (no caching yet).

For a quick check without Blender: `python -m py_compile` the tree, or import `poser_tools` under a
stubbed `bpy` and call `register()`/`unregister()` — that catches class-tuple typos and bad import
paths, though not runtime behavior.
