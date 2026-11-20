# Poser Tools

Blender 5.2+ add-on (extension, `id = "poser_tools"`, GPL-3.0-or-later). All source lives in `poser_tools/`.

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
  `core/cr2/obj_io.py`, `core/cr2/mesh_correspondence.py`, `core/cr2/injection_package.py`,
  `core/cr2/injection_scan.py` (a cheap brace-walking listing of a package's morphs for the picker;
  must agree with `CR2Parser`, see its tests), `core/cr2/library_index.py` (Phase 7.3a: classifies
  the owner's whole Poser library into injection/removal entries and builds the asset-index
  manifest; same rule, its sniff is tested against the parser), `core/cr2/mat_pose.py` (Phase 8: MAT pose reading and slot matching), `core/cr2/injection_job.py` (7.7: Apply's off-thread job), and `core/cr2/apply_injection.py` (morph-injection Phases 1 and 3, see below) live in the same package
  but are original `poser_tools` code, not adapted from `cr2_importer` — GPL-3.0-or-later, not MIT.
  Same no-`bpy` / plain-`pytest` discipline (`tests/test_mesh_correspondence.py`,
  `tests/test_injection_package.py`, `tests/test_apply_injection.py`).
  Which license a new `core/cr2/` file gets is a judgment call, not automatic just because it's in
  this package — MIT when it's a real adaptation of specific `cr2_importer` logic (like
  `actor_vertex_index.py`'s local-index convention, `poser_paths.py`'s `PoserPathResolver`, or
  `runtime_index.py`'s Poser Library scan/cache architecture, confirmed by reading `cr2_importer`'s
  own code), GPL when it's original code solving a problem `cr2_importer` never had (like
  `mesh_correspondence.py`, needed only because this add-on injects into an already-imported mesh
  rather than building one from scratch, `injection_package.py`'s `readScript`-following —
  `cr2_importer`'s own `pz2_parser.py` only *detects* that an orchestrator file exists, it never
  actually follows one — or `poser_library_prefs.py`, which reads Poser's own `LibraryPrefs.xml`,
  something `cr2_importer` never does at all).
  Outside `core/cr2/` because they use `bpy`: `core/shader_translator.py` and `core/material_builder.py`
  (Phase 8 MAT poses) are MIT, adapted from `cr2_importer`'s `core/shader_translator.py` and
  `core/material_pipeline.py`, and maintained here from now on, like the parser.

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
- **Legacy Daz3D detection is automatic, not a manual checkbox** (`docs/handoff-shapekey-improvements.md`
  Phase 6) — an FBX carries no figure identity, but the raw shape-key names do: real M3/M4 exports
  are reliably `p`/`PBM`-prefixed (410/637 on Aiko3) where modern figures have zero such names
  (LaFemme, Kira). `core/functionsShapeKeys.py`'s `detect_legacy_daz()` sniffs this before
  consolidation runs; `resolve_legacy_daz()` turns an `'AUTO'`/`'ON'`/`'OFF'` mode into the bool
  `consolidate_poser_shapekeys()` uses, logging the decision either way. Both the import dialog and
  the manual re-run panel expose this as an `EnumProperty` defaulting to `AUTO` — kept as a real
  override, not removed outright, since 20 years of community Poser content can defy the heuristic.
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
- **Blender 5.1.2 crashes multi-figure FBX imports, and the add-on doesn't work around it.** Blender
  bug #156097 in `BM_mesh_bm_from_me()` (behind both `bm.from_mesh()` and entering Edit Mode)
  corrupts a mesh's UV / shape-key CustomData, so the 2nd or 3rd figure's `remove_loose_verts()`
  crashed. Fixed upstream in PR #156302 for 5.2, not backported. A May 2026 branch tried four
  workarounds (setting the active/render UV map, stripping UV layers, a sentinel UV layer plus
  stripped shape keys); all were dropped once 5.2 shipped, the crash having stopped on 5.2+. Keep
  `remove_loose_verts()` on plain BMesh. v1.0.2 raised `blender_version_min` to 5.2 (owner,
  2026-11-20), so no supported Blender has the bug; don't revive the workarounds.

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
`core/cr2/`. Phases 1-3, 5 and 6 are shipped, plus Phase 7.0/7.0b (path resolution); the rest of
Phase 7 is scoped, not built. Phase 4 (PMD binary reader) is **paused indefinitely** —
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
- Phase 5.4: injected shape keys are named after the CR2 channel's own `name` property (e.g.
  `BrowHeavy`) instead of its cryptic `internal_name` (e.g. `PBMDC_39`) — `core/cr2/name_match.py`'s
  `display_name_for()` resolves it, falling back to `internal_name` if a channel never set one.
  When a group's actors disagree on the label, the one on the channel with the most deltas wins,
  not the first listed (a small `p`-prefixed partial actor often comes first: `pEarDeer` on the
  neck, `EarDeer` on the head).
  `internal_name` is still the actual CR2 lookup key and is still checked (alongside the display
  name) for "already applied" idempotency, so a mesh injected before this change doesn't get a
  duplicate morph under the new name.
- Phase 5.5: **Apply Morph Injection** no longer requires two raw file-browser picks. Add-on
  preferences (Edit > Preferences > Add-ons > Poser Tools) hold one or more Poser Runtime roots
  (`properties/poserToolsPreferences.py`), importable in one shot from Poser's own
  `LibraryPrefs.xml` (`core/cr2/poser_library_prefs.py`, `poser.runtime_roots_import_from_poser`)
  instead of retyping each one by hand. `core/cr2/runtime_index.py` (MIT, adapted from
  cr2_importer's own Poser Library browser, simplified — no folder allowlist since real injection
  content lives under vendor-prefixed folders like `!DAZ` that cr2_importer's own scanner skips; no
  thumbnails or content-sniffing, since the picker is a text search, not a thumbnail grid) scans
  those roots for figure CR2s and `.pz2`/`.p2z` injection candidates. The popup's two fields are now
  backed by `prop_search()` dropdowns (the same searchable-dropdown widget behind Blender's own
  Material/Vertex Group/Shape Key pickers) — picking a figure resolves its reference OBJ
  automatically via the CR2's own `figureResFile` (`core/cr2/cr2_parser.py`'s `resolve_geom_file()`,
  a real pre-existing function fixed in the same change — it imported a module that only exists in
  `cr2_importer`, so it silently no-op'd until now). The raw manual-path fields are kept for a
  setup with no Runtime root configured or content outside it; a pick from the lists wins over them
  (changed after UAT: the path field is remembered by Blender and was silently overriding new picks,
  so `injection_filepath` is `SKIP_SAVE` and the lists start empty each run).
- Phase 6: **Remove Morph Injection** (`poser.remove_morph_injection`,
  `operators/removeMorphInjection.py`) reverses an injection by name-matching, not geometry — no
  reference OBJ, no vertex-correspondence build. Poser's own `RemDeltas.*.pz2` files (shipped
  alongside most `InjDeltas.*.pz2` ones) just re-declare the same channel with zero deltas and a
  literal `name -` placeholder; since that carries no real name to match against, `_apply_morphs()`
  now records `internal_name -> shape-key name` on `mesh["poser_injected_morphs"]` for every morph
  it creates, and Remove prefers that record over the picked file's own (possibly placeholder) name.
  Works whether pointed at the original Inj file or its Rem counterpart. Two vendor Rem files
  (`DC_40_EarDeer`, `DC_42_EarFeline`) name a different channel than their Inj file, so a Rem file
  that matches nothing falls back to its same-named `InjDeltas.*` sibling (`paired_injection_path()`).
  Reuses the same
  Runtime-library `prop_search()` picker from Phase 5.5 and the existing
  `injection_package.py`/`name_match.py` pipeline — no new `core/cr2/` module.
- Phase 7.0: `core/cr2/poser_paths.resolve_poser_path()` matches colon-path components **ignoring
  case** (exact match first, on-disk casing returned). Real orchestrators write
  `:Runtime:Libraries:Pose:` for a folder that is `libraries/pose` on disk, so on a case-sensitive
  filesystem a literal lookup missed ~71% of failing `readScript` targets and Apply silently loaded
  nothing for e.g. `! All Head Morphs.pz2` (256 of 257 unresolved). `load_injection_package()`
  passes a per-load `dir_cache`. 7.1: the vertex correspondence is cached on the mesh
  (`mesh["poser_correspondence"]`, `core/cr2/correspondence_cache.py`), keyed on a SHA-256 of the
  reference-OBJ vertices + basis positions + format version, so an edited mesh or different OBJ is a
  miss, never a stale hit; real Aiko3: 33s → 0.4s, bit-identical output. Bump `CACHE_VERSION` if
  `build_vertex_correspondence()`'s results or the blob format change. 7.0b: `load_injection_package(path, extra_roots)` searches the add-on
  preferences' Runtime roots (Apply and Remove pass `get_runtime_roots(context)`) after the file's
  own Runtime, since a package often names files that live in a different library, and a package
  that yields no morphs because its files weren't found now says so (`no_morphs_report()`). Phase 7
  (thumbnail library via Blender's Asset Shelf) is scoped in
  `docs/handoff-morph-injection.md`; 7.0 to 7.4 are built (7.2, 7.3 and 7.4 below).
- Phase 7.4: **using a package from the library** (`poser.use_morph_package`,
  `operators/useMorphPackage.py`; `ui/packageAssets.py`). One operator behind the Asset Browser's
  right-click entry, a side panel there, and the one Poser Library shelf (All, the default / Injection / Removal / Materials
  switch, `WindowManager.poser_shelf_kind`: Blender shows one shelf per 3D View and can't switch between
  shelves that poll alike, so kinds share it; opened from
  View → Asset Shelf). It reads the asset's `poser_root`/`poser_rel_path`/`poser_kind` through
  `context.asset.metadata` (read live on 5.3; `core/library_build.resolve_package_asset()` falls back to
  tags + description and to the other Runtime roots). Injection opens the 7.2 picker when the mesh
  remembers its reference OBJ, else Apply's popup with the package pre-picked; removal runs Remove
  directly (`call_nested(..., execution='EXEC_DEFAULT')`). The asset is read through `context_asset()`
  so a headless script can patch in a stand-in: `temp_override(asset=…)` doesn't take one. No drag and
  drop: Python has no hook for an asset dropped from the Asset Browser, and a Scene has no drop target.
- Phase 7.3: the **library index** (7.3a/7.3b the engine, 7.3c the operator and Preferences UI; the shelf is 7.4).
  `core/cr2/library_index.py` classifies the owner's whole Poser library (16,405 entries, ~10 s warm,
  ~40 s cold) into injection/removal and builds a manifest of 3,816 shown entries in 133 source
  folders, incrementally (20 roots; 3,783 in 129 on the owner's current 13 roots). Only files in
  Poser's own category folders under `Runtime/Libraries` (`POSER_LIBRARY_CATEGORIES`: pose, face, ...)
  are entries, as in Poser's Library palette: vendor folders beside them (`!DAZ`, `!Adz`) hold the files
  packages `readScript`, and a file-name rule alone missed `!Adz`'s `<morph>.InjDeltas.pz2` naming.
  The Apply/Remove dropdowns still list those files on purpose (manual escape hatch). A package any of
  whose files sets a bone channel (`rotate`/`translate` X/Y/Z) or carries Poser's pose-file `thighLength` line
  is an `"armature"` pose and hidden, even when
  it also injects (V3 Spellcaster, Channing "Fit" packages that move the hip): the library is morph packages
  only, poses stay out (FBX bone rolls). MAT poses are planned as Phase 8 in the handoff doc. Catalogs are
  `<Runtime>/<Category>/...` like Poser's Library palette: the Runtime's Preferences Label, else its folder
  name (`runtime_names()`), and the category folder's first letter capitalized (`pose`/`Pose` merged).
  The names are layout (`manifest["layout"]`, outside the signature), so a renamed Label reuses every
  classification and rewrites only that Runtime's files; `status_is_stale()` compares the layout too. `core/library_build.py` turns it into asset `.blend` files by running
  `core/library_assets_child.py` in a **child Blender process** (never in the user's session: building
  asset-marked stand-ins there would touch their file, undo stack and UI), writes the catalog file, saves
  the manifest last, and registers the folder as an asset library (`ensure_asset_library`, which must
  check for an existing entry: `new()` twice makes `Name.001`). Everything goes under
  `user_resource('CONFIG', 'poser_tools/library')`, never into the Poser content. `core/library_build.py`
  has no module-level `bpy` so it is pytest-able; the child itself is verified by running it, headless,
  on 5.0.1, 5.2.4 and 5.3.0 and reading every asset back. **Stand-ins are `Scene`s, and must stay a type
  the Asset Browser lists** (Action, Brush, Collection, Material, Node Tree, Object, Scene, World): an
  `Image` library registers, shows its catalogs and then "No items", which no headless check can see.
  The library's name is a preference (**Library Name**, default "Poser", was "Poser Morph Packages"); the add-on
  finds its own entry by folder (`library_build.find_asset_library`: this version's `poser_tools/library`, else
  any `poser_tools/library`, as preferences carried over from another Blender version point at that one), so
  renames, the old name and version upgrades never leave a second entry.
  Scene was picked because it has no drop target and no other consumer; Object/World/Material are drop
  targets, Action feeds the Pose Library, NodeTree the Add menus. A removal is a channel that
  re-declares its delta list empty (`indexes 0`), not just `name -`: `Hide.*` visibility files have
  `name -` too (`library_index.sniff_leaf`, `ScannedChannel.declares_deltas`). When the index changes,
  look at it in a real Blender window (throwaway `BLENDER_USER_CONFIG`, screenshots), not only through
  `bpy.data.libraries.load`. 7.3c: `poser.build_library_index`
  (`operators/libraryIndex.py`, modal like Apply: a worker thread that never touches `bpy`, a timer on
  the main thread for progress, Esc cancels, synchronous under `--background`) and
  `poser.remove_library_entry`, driven from Preferences (`properties/poserToolsPreferences.py`
  `_draw_library`). A finished build runs `asset.library_refresh` in every open Asset Browser and
  shelf region (`_refresh_open_asset_lists`): Blender doesn't watch the folder, and an open shelf
  otherwise keeps listing deleted assets until restart. It runs from a one-shot timer
  (`refresh_open_asset_lists_later`): the build is started from the Preferences window, a temporary
  screen, and `temp_override` from there into another window raises `TypeError`. Build status is a small `status.json` beside the manifest so drawing never loads
  the manifest; it goes stale when the roots, asset format or Blender version change. Registering the
  library edits the user's preferences, so it happens only from that explicit click, on the main
  thread. `INVOKE_DEFAULT` runs `execute()` under `--background`, so the interactive path is the same
  `_work()` called inline; the modal wrapper itself needs the GUI.
- **7.3d: old Mac `.rsr` thumbnails** (`core/mac_pict.py`, stdlib only, imported by the asset child from its
  own folder): a resource fork holding one PICT v2; the four PackBits bitmap formats on the owner's drive are
  decoded, anything else refused. Checked against ImageMagick on all 8,560 PICTs (identical except the 16-bit
  ones' 5-to-8-bit widening, which here replicates bits). All 1,644 `.rsr`-only library entries now have their
  picture.
- **7.7: Apply never blocks on a big package** (`core/cr2/injection_job.py`, no bpy). The package parse (26 s on V3's
  259 head morphs) and each morph's positions (10 s) ran on the main thread around the 5.1 threaded correspondence;
  an `InjectionJob` now does parse, OBJ load, cache check/build and positions on the worker. The operator reads the
  mesh first (reference key or vertices, key names, the cache blob), then its timer drains a bounded queue of
  finished morphs into shape keys for at most 0.1 s per tick (a morph's positions are a full mesh copy, so never all
  held at once). Input passes through until keys are being added, then is held so the mesh can't be edited between
  two; Esc and failures remove the keys that run added (and a Basis it created; Basis is only made when a morph
  lands). Headless runs the same job inline. Longest main-thread stall measured in a real window: 0.66 s. The
  figure's OBJ comes from `cr2_parser.read_figure_res_file()` (brace-depth line walk, agrees with the parser on all
  1,370 CR2s on the owner's drive) instead of a 6 s full parse on the first dialog's OK.
- **Phase 8.4: material collections and single materials.** `.mc6`/`.mcz` (a `mtlCollection` of named
  materials) are kind `"material"`, applied by `poser.apply_mat_pose` like a MAT pose (same matching, preview,
  backup). `.mt5`/`.mz5` (one material under `actor $CURRENT`, nearly always named `Preview`) are kind
  `"slot_material"`, applied by `poser.apply_material_to_slot` (`operators/applyMaterialToSlot.py`, label "Apply
  Single Material") to the mesh material picked in its dialog's searchable **Replace** field (starts on the active
  slot's; a scripted call without `target_material` uses the active slot), name in the file ignored. Poser applies
  one to the selected part; Blender has no selected part, so the dialog asks (owner, 2026-11-19). `.mcz`/`.mz5`
  are gzip (`poser_io.COMPRESSED_EXTENSIONS`); `runtime_index` lists all four (`CACHE_VERSION` 2);
  `library_index.classify_entry` judges them on `_is_mat_pose` alone (`MANIFEST_VERSION` 7). All 4,301 on the
  owner's drive parse; 18 hold no material (show/hide toggles, pose resets, 0-byte placeholders) and stay hidden.
  Owner (2026-11-19): extending the library with kinds that reuse what's built is fine; 7.6 was tabled for
  reinventing Blender's search, not for touching the Browser.
- **Phase 8.3: textures found the way Poser does** (`core/cr2/texture_paths.resolve_texture`, used by
  `material_builder.make_image_loader`): as written, then relative to Runtime / Runtime:Textures, a Windows
  path's Runtime tail, a `.bum`'s source image, and the file name across the roots (shared trailing folders,
  then the MAT's own root). NO_MAP is no texture. Every fallback is listed in the MAT report.
- **Phase 8.2: MAT poses in the library.** Kind `"material"` (`library_index._is_mat_pose`: one file of
  `material` blocks and nothing else, judged on the whole file) is shown, every MAT included, props and
  clothing too (owner). On the shelf behind its Materials switch (MAT Poses before 8.4); `poser.use_morph_package` (idname kept for its bindings) sends MAT
  assets to `poser.apply_mat_pose`'s dialog. `read_head()` normalizes `\r` line endings: old Mac files
  slipped past every `^`-anchored pattern.
- **Phase 8.1: MAT poses** (`poser.apply_mat_pose`, `operators/applyMatPose.py`, `ui/applyMatPose.py`;
  `core/cr2/mat_pose.py` no bpy; `core/material_builder.py` and `core/shader_translator.py` are bpy, MIT,
  adapted from `cr2_importer`). The FBX import names slots `<object>_<material>`, so matching strips that
  prefix (and `.001`) and compares names exactly; unmatched names are reported, never guessed, and
  `Preview` (Poser's viewport material) is ignored. Owner's calls: rebuild the slot's material in place,
  and ask first; the dialog is the confirmation (it previews the match), and the first MAT on a material keeps
  a fake-user copy `<name> (original)` (`mat["poser_mat_backup"]`), never overwritten by later MATs. The
  translator now reports node types it has no mapping for (Poser `toon`, `simple_color`) instead of
  skipping them silently. Since 8.4 it also translates `ambient_occlusion`, `clouds` and `colorramp` as
  compound nodes (`_post_init_node` builds helper nodes and returns routes for the inputs that live on them;
  a ramp with a linked colour becomes a mix chain, since ramp stops can't take links). Owner: `fastscatter`,
  `granite`, `s`, `t` and `toon` have no equivalent. Parsing needed no port of `cr2_importer`'s `PZ2Parser`; `CR2Parser` reads every
  MAT on the owner's library after two fixes (spaced material names, a bare `file` line in a shader node).
- **Package dials** (owner, 2026-11-18): a character package `readScript`s its morphs and then dials them
  (`valueParm PBMTone k 0 0.232` on BODY). `core/cr2/injection_package.package_dials()` reads those values
  from the package's own files, the ones holding `readScript`, never from the delta files, whose `k 0 0`
  would reset a dial. `match_dials_to_shape_keys()` finds each key the way Apply finds a present morph
  (record, label, internal name, `p`-less label). Apply sets them after injecting, morphs already on the mesh
  included, widening the slider (Blender caps it at ±10); non-dial settings (`propagatingScale`, bones) are
  reported, not applied. When nothing is new but the package dials, the picker hands Apply
  `selected_morphs="[]"`, which means "set the dials only".
- Phase 7.2: **morph picker** (`poser.pick_injection_morphs`, `operators/pickInjectionMorphs.py`,
  `ui/pickInjectionMorphs.py`). Apply's popup (package + figure) hands over to a second dialog
  listing the package's morphs; its OK calls `poser.apply_morph_injection` with `selected_morphs`/
  `selected_leaves` (JSON), which then parses only those files. `core/cr2/injection_scan.py` lists a
  package's morphs without parsing deltas (~1 s vs. ~9 s for the 259-morph V3 head package); it is a
  second reader of the CR2 format, so tests assert it agrees with `CR2Parser`, and Apply reports any
  chosen morph the parser can't find. A scripted `bpy.ops.poser.apply_morph_injection(...)` call
  with no selection still applies everything (`use_morph_picker` is only set by the interactive
  `invoke()`). **Headless note:** under `--background`, `INVOKE_DEFAULT` runs `execute()`, not
  `invoke()`, so the dialog flow can't be exercised without a GUI; the scan, classification,
  estimate and selected-only apply are tested instead. **Nested operators lose their reports:** a
  warning/info from an operator started with `bpy.ops` inside another one is not shown, and an error
  raises in the caller, so these operators report through `say()` and start each other through
  `call_nested()` (both in `operators/applyMorphInjection.py`), which re-issue the message from the
  UI-invoked outer operator. Use them for any new operator in this chain (the 7.4 shelf click too).

## Branching

`main` carries finished, releasable work only. A large feature that spans many PRs gets a long-lived
feature branch: its PRs target that branch (branch new work from it, not from `main`), and the
branch merges into `main` once the feature is done. Small fixes unrelated to a feature can still go
to `main` directly.

- `feature/poser-library` holds the CR2 parser, morph injection, the Poser asset library and MAT /
  material support (PRs #12 onward; its own CLAUDE.md and `docs/handoff-morph-injection.md` describe
  it). Those PRs had merged straight into `main`; on 2026-11-20 `main` was reset to before #12 (the
  merge of #11) plus PR #22's release-index commit, the one change from that range that stands on
  its own, and force-pushed (owner's call). Nothing was lost: the old `main` is that branch's history.

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
   **Legacy Daz3D Figure** defaults to **Auto-Detect** and doesn't need to be touched for an M3/M4
   figure — only override it (**Force On**/**Force Off**) if detection guessed wrong. Check: mesh +
   armature import, material-slot order restored, loose verts gone, bone rolls sane, neck centered,
   conforming figures split into their own armatures. Shape keys: parent/child morphs collapsed
   into single sliders, no `.001`-suffixed or `JCM…` keys left, a **Poser Shapekey Report** text
   block written (including a `Legacy Daz3D naming: auto-detected yes/no` line),
   `mesh["poser_shapekey_merges"]` set.
3. Re-run check: **Fix Poser Shapekeys** on the same mesh → "already consolidated" info, no-op
   (`obj["morphs_consolidated"]`). Untick **Consolidate Shape Keys** in a fresh import → raw shape
   keys, no report, then the button does the work.
4. Armature selected → **Rename Armature Bones** adds `.L`/`.R` suffixes. Mesh selected →
   **Rename Weight Groups** applies the matching rename to vertex groups.
5. First, in Preferences > Add-ons > Poser Tools, add a Poser Runtime root (or click **Import
   Runtime Roots From Poser** to pull them from `LibraryPrefs.xml` automatically). Mesh selected →
   **Apply Morph Injection**: the popup's **Figure (CR2)** and **Injection Package** fields are
   searchable dropdowns sourced from those roots — pick a figure and its reference OBJ resolves
   automatically (no manual `.obj` browse), pick an injection package the same way. The raw manual
   path fields below still work when nothing is picked from the lists (no Runtime root configured, or
   content outside it); a list pick wins, and the dialog says so if both are filled. A package with several morphs then opens **Choose Morphs to Apply**: everything new
   is ticked, filter/Select All/Select None work, locked rows (already present, JCM, `.pmd`, no
   deltas) are greyed with a reason, and the box shows the estimate; OK applies only the ticked
   ones (the report says so). Expect: new shape keys named after the package's morphs, a **Poser Morph Injection
   Report** text block (including a total-time line), `mesh["poser_reference_obj"]` and
   `mesh["poser_reference_cr2"]` set. Expect roughly 30-60s on a 70k-vertex figure, dominated by the
   one-time vertex-correspondence build — the cursor shows a counting-up percentage (matching
   **Import Poser FBX**'s own progress cursor) and the status bar shows both the percentage and
   elapsed seconds the whole time, window never greys out, Esc cancels cleanly. Re-run on the same
   mesh → the figure picker pre-fills the previous choice, reports "already present" for the same
   package, no duplicate keys, and now finishes almost instantly (the vertex correspondence is cached on
   the mesh; the report's **Vertex correspondence:** line says whether it was reused or built).
6b. Preferences → Add-ons → Poser Tools, with Runtime folders set: **Build Library Index**. The
   cursor shows a percentage and the status bar names the phase (reading files / classifying packages /
   writing asset files), Esc cancels and changes nothing; on success an INFO message, a **Poser Library
   Index Report** text block, and the panel shows the package count, thumbnail/placeholder counts and
   "registered". An Asset Browser pointed at **Poser** (the default **Library Name**) shows the packages by folder. Rename it in
   Preferences: the registered entry follows, found by its folder, never duplicated.
   Run it again: seconds, "0 written". Change the Runtime folders or a Label: the panel says to update; a renamed Label rewrites only that Runtime's files. The Asset Browser lists one catalog per Runtime, each with a capitalized category (Pose) below.
6c. With that library built and a mesh active: in the Asset Browser, right-click a package →
   **Apply to Active Mesh...** (or the button in the side panel's **Poser Morph Package** box) opens the
   morph picker (or Apply's popup with the package picked, on a mesh never injected into); a removal
   package's **Remove from Active Mesh** removes its keys at once, Ctrl+Z restores them. Double-clicking
   one of our packages does the same (a keymap item; other libraries' assets keep Blender's own
   double-click). `Window.event_simulate` can't produce a double-click, so test it by hand. In the 3D
   Viewport, View → Asset Shelf shows the Poser Library shelf in Object Mode; its settings popover (or the
   sidebar's Poser Library Shelf panel) switches All / Injection / Removal / Materials; every real thumbnail carries
   its kind badge (blue +, amber −, violet swatch); clicking a tile
   does the same.
6. Same mesh → **Remove Morph Injection**, pick the same package's `RemDeltas.*` file (or the
   original `InjDeltas.*` one — either works) via the same searchable dropdown. Expect: near-instant
   (no correspondence build), the matching shape key(s) gone, a **Poser Morph Removal Report** text
   block. Re-run with the same package → reports 0 removed / already not present, no error.
7. Aiko3 mesh → **Materials** → **Apply MAT Pose or Collection**, pick Lady Xtaci's `! MAT Lady Xtaci !.pz2`: the dialog
   says "Replaces 31 of Figure's 34 materials" and lists `SkinLeg` as having no slot; Apply → textured
   materials in Material Preview, 31 `... (original)` materials with fake users, a **Poser MAT Pose Report**.
   Ctrl+Z restores them. A second MAT adds backups only for materials the first didn't touch. With the
   library index updated, the same MAT in the Asset Browser (double-click, right-click → **Apply Materials to
   Active Mesh...**) or on the shelf switched to Materials opens that dialog with the file picked. A collection
   (`Aiko3 ToonShade.mc6`) goes through the same dialog. **Apply Single Material**, pick a `.mt5`/`.mz5`
   (`Blue Jeans.mt5`) and a material in **Replace** (starts on the active slot's): Apply replaces only that material
   and keeps `<name> (original)`. From the library, a single material (green badge) opens that dialog; searching "single" / "collection" narrows to one type (tags).

For a quick check without Blender: `python -m py_compile` the tree, or import `poser_tools` under a
stubbed `bpy` and call `register()`/`unregister()` — that catches class-tuple typos and bad import
paths, though not runtime behavior.
