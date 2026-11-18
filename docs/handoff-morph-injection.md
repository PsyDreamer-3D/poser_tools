# Morph Injection: Import 3rd-Party Poser Morph Packages

Written for a fresh session with no prior context. Read `../CLAUDE.md` first, then
`docs/handoff-shapekey-improvements.md` (the CR2 parsing/matching infrastructure this builds on),
then this document.

## Why this exists

The original intent behind `handoff-shapekey-improvements.md` Phase 5 (CR2 cross-reference) got
narrowed, while working it, into "use the CR2 to get ground-truth names for grouping shape keys
that already exist on an FBX-imported mesh." The actual goal was bigger: **let a user import a
3rd-party Poser morph package (a `.pz2` injection template, e.g. "Aiko3 SP All Morphs") and add
those morphs as new shape keys to a mesh already imported by `poser_tools`** — morphs that were
never part of the original FBX export at all, because the content wasn't loaded in Poser when the
figure was exported.

This is a materially different, bigger capability than consolidation. It builds directly on
`core/cr2/` (`cr2_parser.py`, `name_match.py`) but needs new infrastructure `core/cr2/` doesn't
have: a way to place *new* geometry data onto a mesh that already exists in Blender.

## The core problem, and why it's solvable

A PZ2/PMD injection's deltas are indexed **per actor, in Poser's local vertex order**
(`d 47 dx dy dz` = "actor X's local vertex 47"). To turn that into a Blender shape key on a mesh
Blender's FBX importer already built, we need to know which *Blender* vertex that is — and nothing
in `poser_tools` or `cr2_importer` already solves this (`cr2_importer` only solves it when it
builds the mesh itself, from a `.obj` file, so it controls the indexing end-to-end; it never
injects into a mesh someone else built).

**Investigated and resolved:** parsed `blaiko3.obj` (Aiko3's reference geometry, from the CR2's own
`figureResFile`) and `LaFemme1.obj` (LaFemme's) and compared each against its poser_tools-imported
FBX mesh:

| | Aiko3 | LaFemme |
|---|--:|--:|
| OBJ vertex count | 72712 | 24478 |
| Raw FBX import count | 74826 | — (already matched) |
| After `remove_loose_verts()` | 72712 | 24478 |
| **Raw index correspondence** (Blender vtx *i* == OBJ vtx *i*) | **false** — residual mean 0.42, 0% within 1mm | same, not re-tested (already disproven structurally) |
| **Position correspondence** after coarse align | **100%**, residual ≈0.000000 | **100%**, residual ≈0.000000 |
| Fitted scale | 2.62128 | 2.62128 |
| Fitted axis mapping | permute (0,2,1), signs (+,−,+) | permute (0,2,1), signs (+,−,+) |
| Unique matches | 72664 / 72712 | 24478 / 24478 (no collisions) |

Same transform, same scale, two unrelated figures from different vendors (DAZ vs RPublishing) —
this is a fixed, general Poser→Blender FBX-export/import convention (it comes from the exporter's
axis/scale settings, not anything figure-specific), not a coincidence. The fitted scale
(2.62128) independently matches `cr2_importer`'s own hardcoded Poser→Blender scale constant
(`2.6213`, in `APPLY_OT_poser_pz2_pose`) — further confirmation this is the real, correct
transform.

**Conclusion:** vertex correspondence is recoverable, cheaply (well under a minute on a 72k-vertex
mesh, using Blender's own `mathutils.kdtree`), *provided we have the figure's reference `.obj`* —
the same file the CR2's `figureResFile` already names. That's the one real external dependency,
not a coding blocker.

> **Update after Phase 1 shipped:** re-verifying through the productized module (below) found
> 97.72% / 68.11% match, not the 100% this table reports — this scratch table's "100%, residual
> ≈0.000000" predates fixing a bug in the *scratch harness itself* (world-space vs. raw local
> vertex coordinates, see Phase 1's "Real-data verification"). The permutation/signs/scale figures
> here still check out (independently confirmed 3 more times since). Left as-is for the historical
> record of how correspondence was first shown to be solvable at all; treat Phase 1's own numbers
> as authoritative for match rate.

## Real injection packages, surveyed

Inspected three real 3rd-party packages (owner-supplied paths, all under a mounted Poser content
library) to find out what format(s) we'd actually need to parse:

1. **`!A3-H3 MORExp1 INJ`** (Aiko3/Hiro3 morph pack) — a top-level orchestrator `.pz2`
   (`readScript`s a list of per-morph files) → each per-morph file
   (`InjDeltas.DC_39_BrowHeavy.pz2`) has its deltas **inline**, plain CR2-grammar text
   (`targetGeom PBMDC_39 { name BrowHeavy ... deltas { d 60 ... } }`). Fed directly into our
   **existing, unmodified** `core/cr2/cr2_parser.py` — parsed correctly (2295 deltas, actor
   `head`, internal `PBMDC_39` / display `BrowHeavy`) with zero new code.
2. **`!SP All Morphs INJ`** (Stephanie 3 morph pack) — same shape: orchestrator → per-morph inline
   delta files, plus a `valueOpDeltaAdd` ERC link back to a BODY dial (grammar our parser already
   handles).
3. **`La Femme/Body Morphs/INJ Body Morphs Pro.pz2`** — different: opens with
   `injectPMDFileMorphs ":Runtime:...:INJ Body Morphs 1R1.pmd"`, then a series of
   `createFullBodyMorph <Name>` directives (not seen before — likely tells Poser to create the
   BODY-level master dial for each name), then `actor BODY:1 { channels { targetGeom
   Shldr_Gap_ADJ_R { ... numbDeltas 25892 useBinaryMorph 1 } } }` blocks with **no inline
   `deltas {}` block at all** — the real per-vertex values live entirely in the external `.pmd`
   binary.

**Two of three real packages need no new parser** — just the correspondence + placement
infrastructure below. Only the PMD-referenced style (package 3, likely most RPublishing/modern
content) needs a binary reader, which nobody (including `cr2_importer`) has built. That's real,
separate work — split into its own later phase rather than blocking everything on it.

One more practical wrinkle confirmed while reading these: orchestrator files reference sibling
content via Poser's own `:Runtime:libraries:...` logical path convention, not a real filesystem
path. Resolving it doesn't need a new user setting, though — every orchestrator file examined
lives *inside* its own `Runtime` folder (e.g. `.../Base Figures/Runtime/libraries/pose/!A3-H3
MORExp1 INJ/! All Morphs.pz2`), so the root is always the nearest ancestor directory literally
named `Runtime`, walking up from the file itself.

## Phases

### Phase 1 — Vertex correspondence module ✅ shipped

**Goal:** given a reference `.obj` (Poser-native vertex order) and an already-imported, already-
`remove_loose_verts()`-cleaned poser_tools mesh, produce an OBJ-vertex-index → Blender-vertex-index
mapping.

**Built:** `core/cr2/obj_io.py` (`load_obj_vertex_positions`) + `core/cr2/mesh_correspondence.py`
(`build_vertex_correspondence`, plus internal `_coarse_align`/`_grid_nearest_neighbor`) +
`tests/test_mesh_correspondence.py` (synthetic round-trip, duplicate-target, and unmatched-point
cases, no `Test_Poser_Assets/` dependency). Pure NumPy, no `bpy`/`mathutils.kdtree` — went with the
numpy grid-bucket nearest-neighbor over `mathutils.kdtree` per the recommendation below, keeping
`core/cr2/` entirely `pytest`-testable without launching Blender.

**Design, as built:**
- Coarse alignment *re-derives and verifies* the transform per call (mean/RMS-radius centroid +
  scale, with a generous MAD-based gross-outlier filter, then a 48-way axis-permutation/sign-flip
  search scored by a radius-capped sampled nearest-neighbor check) rather than hardcoding the
  known scale (2.62128) blind — see "Real-data verification" below for why *median*-based
  statistics (the first attempt) turned out to be the wrong choice for a real body mesh.
- One scale-refinement pass: pass 1's matched pairs feed a least-squares refit of scale (real
  correspondence data, not just aggregate cloud statistics), then a second, final nearest-neighbor
  pass with the refined scale.
- Unmatched/collided vertices are reported, not dropped: `matched_index` is `-1` for anything past
  `match_epsilon`, and `duplicate_targets` names every target claimed by more than one source
  vertex.

**Real-data verification** (manual, headless Blender, Aiko3 + LaFemme — not a committed fixture,
per the plan's own reasoning: this needs an actual FBX-imported mesh):

| | Aiko3 (72712 verts) | LaFemme (24478 verts) |
|---|--:|--:|
| Recovered permutation/signs | (0,2,1), (+,−,+) | (0,2,1), (+,−,+) |
| Refined scale | 2.6367 | 2.6372 |
| vs. `cr2_importer`'s constant (2.62128) | +0.65% | +0.68% |
| Matched within 1cm | 97.72% | 68.11% |
| Mean residual (matched) | 0.0028 | 0.0046 |

Two things worth recording so a future session doesn't re-derive them:
1. **A naive scratch harness will get this badly wrong.** Reading `mesh.vertices.co` right after
   `import_fbx.load()` isn't enough — the unit/axis conversion (`global_scale`, here `0.01`, i.e.
   the standard FBX-declares-centimeters conversion, generic to any FBX and unrelated to Poser)
   lands on the *parent* object (the armature the mesh is parented to), not baked into the mesh's
   local vertex data, and `matrix_world` isn't valid until the depsgraph has evaluated that
   parenting. Call `bpy.context.view_layer.update()` after import and read world-space positions
   (`local_co @ matrix_world`), or you'll compare against raw, un-converted, ~100x-too-large
   numbers and get nonsense (this is exactly what happened on the first real-data run). This is a
   second, separate scale factor from the ~2.62 Poser-native-unit-to-meters constant below —
   they're independent, stacked, unrelated conversions, not a discrepancy in either one.
2. **Median-based robust statistics are the wrong choice for a real body mesh**, despite testing
   fine synthetically. A real mesh's per-vertex radius about its own centroid is heavily
   right-skewed (dense torso/face vertices sit close in, a long thin tail runs out to
   fingertips/toes) — the median is exactly the statistic a skewed distribution makes unstable
   (a handful of stray/unmatched vertices shifts *which* vertex sits at the 50th percentile, and
   because the distribution is steep there, that shifts the value a lot). Mean/RMS over the whole
   cloud is far more accurate here; what it can't tolerate is a genuine gross outlier, which is why
   `_coarse_align` filters by a generous (20 MAD) threshold first, then uses mean/RMS.

**Not fully closed:** exact match rate (97.7% / 68.1%, not literally 100%) is lower than the
original pre-compaction scratch investigation's "100%, ~0 residual" finding for the same file pair.
Tried and confirmed *not* the fix: refining scale further (a 10-iteration refit/re-match
experiment was abandoned as too slow to validate — ~2 min per full 72k-point pass — after the
single-pass refit already landed within 0.7% of the known constant without meaningfully improving
match rate). Owner's call (2026-09-12): accept this as Phase 1's real, verified behavior rather
than keep chasing it — the module recovers the correct transform automatically and resolves the
large majority of vertices with near-zero residual; the remaining gap is most likely seam-duplicate
vertices (Blender's FBX import can weld positions Poser's OBJ format keeps duplicated per UV seam)
rather than an algorithm defect, but that's not independently confirmed. If a future phase needs a
tighter guarantee, look there first before re-tuning the alignment math.

**Performance note:** the pure-Python grid nearest-neighbor is fine for the final match (one pass,
~2 min on 72k points) but the naive 48-candidate coarse-align scoring loop was *not* — it originally
took **12+ minutes and counting** (killed before finishing) because each of the ~47 wrong candidates
paid for an unbounded, exhaustive grid search before being ruled out. Fixed by capping the ring
radius during scoring only (`_SCORING_MAX_RADIUS = 4` in `_coarse_align`) — a wrong candidate now
fails fast, a right one is unaffected (it never needed more than a ring or two anyway) — bringing
coarse-align down to under a minute.

### Phase 2 — Per-actor local-index → OBJ-global-index resolution ✅ shipped

**Goal:** a `d 47 dx dy dz` delta's `47` is local to one actor's geometry group within the OBJ.
Need `47` → the OBJ's *global* vertex index, the same translation `cr2_importer`'s `obj_loader.py`
does via `g <actor>` face groups (`sorted(set(vertex_indices_touched_by_that_actors_faces))`).

**Built:** `core/cr2/actor_vertex_index.py` — `load_obj_actor_vertex_groups(path)` (a minimal OBJ
face/group reader: tracks `g <name>` directives and each face's vertex refs, *not* a full
mesh/material/UV importer like `cr2_importer`'s `obj_loader.py`) and `resolve_local_index(group, i)`.
MIT header, not GPL — unlike Phase 1's `obj_io.py`/`mesh_correspondence.py`, this one really is a
direct adaptation of `cr2_importer` (`obj_loader.py`'s group/face tracking, and specifically
`shape_key_importer.py`'s `sorted(set(group.vertex_indices()))` convention, confirmed by reading
that file rather than assumed) rather than original code solving a problem `cr2_importer` never
had. 9 new tests in `tests/test_actor_vertex_index.py` (28 total in `tests/` now), including one
real-data check: parsed `!Aiko 3.cr2` + `blAiko3.obj` together and confirmed all 55 geometry-bearing
actors (`actor.geom_name or actor.name`) resolve to a real OBJ group — the only miss is `BODY`
(Poser's whole-figure root actor, which has no geometry group of its own; expected, not a bug).

Composed with Phase 1: `local_idx` (Phase 2) → OBJ global idx (Phase 2) → Blender vertex idx
(Phase 1) is the full address of one delta.

### Phase 3 — Apply an inline-delta injection to a mesh ✅ shipped

**Goal:** given an already-imported mesh + its reference OBJ + a parsed inline-delta channel group
(from `core/cr2/cr2_parser.py`, potentially spanning several actors — `core/cr2/name_match.py`
already groups a morph name across actors), build one new Blender shape key with the combined,
correctly-placed deltas.

**Built:**
- `core/cr2/poser_paths.py` (MIT — real adaptation of `cr2_importer`'s `PoserPathResolver`):
  `find_runtime_root()` + `resolve_poser_path()`, resolving a `:Runtime:...` colon-path relative to
  whatever file referenced it.
- `core/cr2/injection_package.py` (GPL — `cr2_importer` only *detects* the orchestrator category,
  never follows it): `load_injection_package()` recursively resolves `readScript` orchestrator
  references (confirmed grammar: `readScript "<colon-path>"`, one token pair) into a single merged
  `Figure`, so a plain single-file package and a many-file orchestrator package both look the same
  to `name_match.py`. Unresolvable references are reported (`unresolved_paths`), never silently
  dropped.
- `core/cr2/apply_injection.py` (GPL — original composition, `cr2_importer` never needed this):
  `build_shape_key_positions()` composes Phase 1 + Phase 2 to place one morph's deltas onto the
  mesh's own current Basis positions. Handles two collision types `cr2_importer` never has to
  (it never reorders vertices): actor-to-actor seam sharing (mirrors `cr2_importer`'s own `claimed`-set
  fix) and Phase 1's own OBJ→Blender many-to-one collision (resolved by keeping whichever
  contending OBJ vertex Phase 1 matched more precisely). Also fixed a real gap surfaced while
  building this: `core/cr2/name_match.py` used to return bare `Channel`s with no actor identity,
  useless for this phase (which needs to know *which actor's* geometry group each delta belongs to)
  — it now returns `(Actor, Channel)` pairs. And extended `core/cr2/cr2_parser.py`'s `Channel` with
  a `uses_binary_morph` flag (from the `useBinaryMorph 1` token, previously silently discarded) so a
  PMD-referenced channel (Phase 4 territory, real deltas but stored externally) is distinguishable
  from a channel with genuinely no deltas at all.

**Real-data verification** (manual, cached Phase 1 point clouds, no fresh Blender import needed —
see "How to apply" in the `project_morph_injection` memory for why caching pays off): applied two
real morphs from the external content-library mount to Aiko3.

| | BrowHeavy (1 actor) | PBMMuscular (19 actors) |
|---|--:|--:|
| Total deltas | 2295 | 14263 |
| Touched (final) | 1795 | 11409 |
| OBJ→Blender collisions resolved | 500 | 1578 |
| Seam collisions skipped | 0 | 932 |
| Unmapped | 0 | 344 |
| Max displacement (Blender units) | 0.0047 | 0.0478 |

Both cases: every raw delta is accounted for in exactly one bucket (touched + collided + seam-skipped
+ unmapped == total deltas exactly, both times) — nothing silently lost or double-counted. PBMMuscular's
344/14263 (2.4%) unmapped rate lines up closely with Aiko3's own Phase 1 miss rate (2.28%), a good
cross-check that this phase isn't introducing new loss on top of Phase 1's own.

**Scope boundary worth being explicit about:** `build_shape_key_positions()` returns a plain numpy
position array, not a real `bpy.types.ShapeKey` — matching the rest of `core/cr2/`'s no-`bpy`,
plain-`pytest`-testable discipline (the same reason `mesh_correspondence.py` doesn't touch `bpy`
either). Actually calling `mesh.shape_key_add(name=...)` + `sk.data.foreach_set('co', ...)` on a real
mesh object is a few trivial lines of glue that belongs with Phase 5's operator, not a `core/`
module. So "build one new Blender shape key" (this phase's stated goal) means "compute the correctly
combined position array a shape key needs" — the object doesn't exist in the scene yet.

**Not verified — the actual visual deformation.** I can confirm indices/counts/collision stats line
up (above), and (once Phase 5 existed) that the shape key's numeric displacement is sane and
non-zero; I can't eyeball whether `BrowHeavy` actually looks like a heavier brow in Blender. That's
the owner's job.

**Explicitly not solved (documented, not a bug):** the real packages' `valueOpDeltaAdd → BODY:1 →
<name>` ERC links target a channel that's never statically declared anywhere in the injection files
themselves (confirmed by grepping the whole package for `createFullBodyMorph` — nothing) — Poser
creates it live, at load time, so there's no canonical/pretty name to resolve from files alone the
way `cr2_importer`'s 4-tier naming logic does. `build_shape_key_positions()` doesn't attempt this;
the shape key's name is the caller's job (the channel group's own internal name, for now) — smarter
naming is Phase 5 polish, not required for this phase's own goal (correctly-placed deltas).

### Phase 4 — PMD binary reader (separate; spike first)

**Goal:** read a PMD's (`PZMD` magic) per-channel delta arrays, for `injectPMDFileMorphs`-style
packages (confirmed: La Femme's "Body Morphs Pro", likely most RPublishing/modern content).

**Explicitly deferred, not scoped yet.** Nobody — not `poser_tools`, not `cr2_importer` — has a
PMD reader today. This needs its own investigation (binary layout is undocumented as far as
anything in this codebase shows) before any design, same discipline as the CR2 parser bug and the
original naming spike got. Do not start on this without a dedicated spike.

### Phase 5 — User-facing operator ✅ shipped

**Goal:** wire Phases 1–3 into an actual operator (a file picker for the injection `.pz2`, applied
to the active mesh), following existing conventions — `self.report()` + a text-block report
(matching `_write_report()` / the `"Poser Shapekey Report"` pattern), `obj["..."]` bookkeeping for
what was already applied so re-running is a no-op or an explicit re-apply.

**Deliberately last.** Each of Phases 1–3 is independently verifiable without any Blender UI; build
and confirm those before wiring up an operator around them, matching how the original shape-key
consolidation phases were built as pure logic first and only wired into `OT_ImportPoserFBX` in a
later, separate change.

**Built:** `operators/applyMorphInjection.py` (`OT_ApplyMorphInjection_Operator`,
`poser.apply_morph_injection`) + `ui/applyMorphInjection.py`, following the exact conventions
above. A two-field popup (`invoke_props_dialog`) collects the injection file and the figure's
reference OBJ (the latter remembered on the mesh as `obj["poser_reference_obj"]`, pre-filled on the
next run). Applies **every** morph the package contains in one run (owner's call — build a
per-morph picker only if this proves too slow in practice; see the timing finding below for why it
turned out not to matter). Skips JCM-named morphs (`core.functionsShapeKeys.is_jcm_shapekey()`,
same reasoning as consolidation) and any morph whose shape-key name already exists (the shape key's
own presence *is* the "already applied" signal — no separate JSON bookkeeping needed, unlike
consolidation's `morphs_consolidated` flag). Reports per-morph touched/unmapped/collision stats to
a new `"Poser Morph Injection Report"` text block, plus any unresolved `readScript` paths and any
skipped-for-being-empty morphs.

**Real end-to-end verification** (not just registration — an actual `bpy.ops.poser.import_poser_fbx`
import followed by a real `bpy.ops.poser.apply_morph_injection` call, headless, real Aiko3 + real
BrowHeavy): new shape key `PBMDC_39` appeared with `touched=2295 unmapped=0 collisions_resolved=0
seam_collisions_skipped=0`, visibly displaced from Basis (max diff 0.0047, 2295 vertices moved,
matching the delta count exactly). Re-running the same call correctly reported "already present,
skipped" and created no duplicate. `poser_reference_obj` bookkeeping round-tripped correctly.

**Important discovery from this real run, worth correcting the record on:** Phase 1/3's own
real-data verification (97.7%/68.1% match rate, and BrowHeavy's earlier-reported
`collisions_resolved=500`) was measured against the FBX-imported mesh *before*
`remove_loose_verts()` — because those scratch scripts called `import_fbx.load()` directly,
bypassing the real add-on's own post-import cleanup. This end-to-end test instead went through the
*actual* `OT_ImportPoserFBX` operator, which does call `remove_loose_verts()` — and on the properly
cleaned-up mesh, the same BrowHeavy morph produced **zero** OBJ→Blender collisions, not 500. The
duplicate-vertex problem Phase 1 characterized appears to be substantially (maybe entirely, for
this case) an artifact of the *unstripped* raw FBX vertex set, not a property of what a real user's
mesh actually looks like. Not re-verified at full-figure scale (would need PBMMuscular through the
real operator, another ~5 minutes) — flagging this for whoever next has reason to revisit Phase 1's
match-rate numbers, since the real, production figure may perform meaningfully better than
documented there.

**Timing finding, relevant to the "apply all vs. per-morph picker" decision:** the ~5 minutes this
real run took is almost entirely the one-time `build_vertex_correspondence()` call (Phase 1) — it
doesn't scale with how many morphs are in the package. A per-morph selection UI would save nothing
on the slow part; the only lever that would (deferred, see "Explicitly not doing" below) is caching
the correspondence across runs on the same mesh.

### Phase 5.1 — stop the operator from looking hung ✅ shipped (2026-09-13)

**Problem, found via real UAT (not a theory):** running the operator interactively made Blender's
window go grey and unresponsive for the ~5 minutes `build_vertex_correspondence()` takes, with no
progress feedback — indistinguishable from a real hang. Owner's own words: "anything longer than
30 seconds to a minute is likely to make a user force-quit." Reproduced and confirmed the algorithm
itself isn't at fault (283s, 100% of vertices matched, well inside the already-documented "~1-5
min" range) — the bug is architectural: `execute()` called the slow function synchronously, so
Blender's main thread couldn't process any events (repaint, progress bar, "still alive" heartbeat)
for the whole call.

**Fix:** `execute()` now branches on `bpy.app.background`. Interactively, the correspondence build
runs on a daemon `threading.Thread` while a `wm.event_timer_add()`-driven `modal()` polls it,
updating `context.workspace.status_text_set()` with elapsed seconds (and accepting Esc to abandon
the wait — the thread itself keeps running silently to completion since Python threads can't be
killed, but it never touches `bpy.data` so this is safe) so Blender's event loop keeps running the
whole time instead of freezing. NumPy's C-level ops release the GIL, and CPython's own
bytecode-level GIL switching keeps the thread from starving the main thread even in
`_grid_nearest_neighbor`'s plain-Python loop — no changes needed to `core/cr2/mesh_correspondence.py`
itself, which stays bpy-free and pytest-covered exactly as before.

**Headless guard, and a real gotcha found while building it:** `context.window is None` is *not* a
reliable way to detect `--background` mode — confirmed against this Blender build (5.2.1 LTS), a
`Window` datablock exists even under `blender --background --factory-startup`. Going modal there
would hang forever (nothing dispatches `TIMER` events without a real event loop). `bpy.app.background`
is the correct, documented flag; `execute()` uses that instead, and the headless branch runs
exactly the old synchronous path (needed for scripted `bpy.ops` verification like this repo's own
e2e checks). Re-verified end-to-end: headless run of `poser.apply_morph_injection` against real
Aiko3/BrowHeavy still produces `touched=2295 unmapped=0`, and a second call is still correctly
idempotent (no duplicate shape key).

**Not done:** no progress-callback/percentage instrumentation threaded through
`mesh_correspondence.py` — elapsed-time status text plus a responsive UI is enough to stop the
operator from *looking* hung; a real percentage isn't worth the added coupling. Correspondence
caching across runs (see the open decision below) is a different, still-deferred optimization —
it would speed up a second run, but the *first* run still has to look responsive while it works.

### Phase 5.2 — make the correspondence build actually fast ✅ shipped (2026-09-13)

**Problem:** Phase 5.1 stopped the operator from *looking* hung, but real UAT confirmed ~5 minutes
is still too slow to ship, and separately noted the wait had "no signs anything was happening"
even with the status-bar text. A dedicated Opus research pass profiled
`core/cr2/mesh_correspondence.py` and found the real bottleneck: `_grid_nearest_neighbor`'s
per-point Python loop pays ~750µs of interpreter overhead per query point for ~20µs of actual
arithmetic, and a query point with no real match can cost 4.8s alone (the ring-expansion loop
rebuilds its whole search cube from scratch at every radius, all the way to the grid's full
footprint, before giving up on something `match_epsilon` was going to discard anyway).

**What shipped, and what didn't (two real dead ends worth recording):**

The agent's own verified prototype vectorized *everything*, including `_coarse_align`'s
48-candidate scoring search (rebuilding it as a fixed-neighborhood or ring-escalating batched
lookup). Reimplementing that here hit a real trap on both synthetic and **real Aiko3 data**: a
uniform grid cell size that's fine on average can still land on a genuinely dense local region —
measured directly on Aiko3, ~4,282 vertices sit inside a single 2.7cm cube near the hip/pelvis
(vs. a median cell occupancy of ~5). A *vectorized batch* search sums candidate counts across every
query point in the batch at once; a wrong candidate's sample points, still centered near the same
origin as the real cloud regardless of the (wrong) axis permutation, can spuriously sweep up that
one dense region once the search radius grows — costing minutes and multiple GB of temporary
arrays for a batch that a *per-point* loop would have paid for individually and cheaply. Two
attempts at fixing this (a fixed-ring lookup, then an adaptive per-point-group escalation) each
reproduced the same blowup in a different shape before the real fix became clear: **don't vectorize
`_coarse_align`'s scoring at all.** Real timing already showed it was never the bottleneck (~13-20s
of the original ~283s) — it's left exactly as it was, per-point loop and all; only the two real
full-mesh passes in `build_vertex_correspondence`, which *were* ~93% of the runtime, were rewritten.

Those two passes vectorize cleanly because they no longer need to search "everywhere, then
threshold" — each pass sizes its grid cell to the actual distance tolerance that pass needs (a
sorted-cell-id grid + `np.searchsorted`, chunked, replacing the per-point loop), so a single fixed
3×3×3-cell lookup is *provably* sufficient (a target within Euclidean distance ≤ `cell_size` cannot
differ from the query's own cell by more than 1 along any axis) — no ring-expansion machinery
needed there at all. Pass 1's tolerance (pre-refit, feeding the least-squares scale refit) needed
its own real-data tuning for the same density reason: `0.02 × target_radius` (the agent's
synthetic-benchmark suggestion) and an initially-chosen "safer" `0.05` both cost real minutes/GB on
Aiko3's dense region — a *larger* tolerance directly means more candidates in that region's cells.
Measured directly against real Aiko3 data: `0.005` and `0.01` both land **100%** of points within
tolerance (i.e. neither shrinks the least-squares refit's trusted point set at all, vs. an
effectively-unbounded old pass 1) at 2.45s and 11.76s respectively for the whole pass; `0.01`
shipped as a safety margin over the tightest value that still worked, for figures whose
coarse-alignment residual might be somewhat worse than Aiko3's. Pass 2's tolerance is tied directly
to `match_epsilon` (with a small margin), which was never the concern.

**Result, real Aiko3 + BrowHeavy, same headless verification as Phase 5.1:** `295.4s → 31.8s`
(9.3x), `touched=2295 unmapped=0` — bit-identical to the pre-rewrite baseline — and the re-run is
still correctly idempotent. Not the ~45x a synthetic benchmark alone suggested, but a real,
verified number against production data, not a projection.

**Known synthetic-only discrepancy (not seen in real data or the existing pytest suite):** a
harsher-than-realistic synthetic stress test (a torso-like dense cluster with 3% of target points
randomly deleted, simulating "many points with truly no correspondence") found 43 of 20,000 final
matches differ from the old, unbounded-search implementation. Root cause: old pass 1's unbounded
search always found *some* nearest neighbor for every point (however far/bad), so effectively every
point counted as "trusted" for the scale refit; the new tolerance-bounded pass 1 correctly excludes
points with no real match within tolerance from that trusted set, which very slightly shifts the
refit's fitted scale in a scenario with enough genuinely-unmatched points feeding it. Arguably a
*more* robust behavior (excluding garbage matches from a least-squares fit), not a regression — and
it did not appear in the real Aiko3 verification (100% matched at pass 1, so nothing was excluded)
or in any of the project's own pytest fixtures. Worth knowing if a future match-rate investigation
finds a small discrepancy on unusually noisy data.

**Also added per UAT feedback (Test 1/2):** the interactive wait now sets the window cursor to
`'WAIT'` (reset to `'DEFAULT'` when done) alongside the existing status-bar text — a status-bar
message alone was confirmed too easy to miss ("no signs that anything was happening"). The
`"Poser Morph Injection Report"` text block now includes a `Total time: {elapsed:.1f}s` line.

**Explicitly not done, still:** correspondence caching across runs (see the open decision below);
importing a morph onto a separate geometry object instead of the active mesh, to apply later —
raised during UAT, owner's own call to defer it (ties into the planned `mesh_tools` shape-key-export
merge, not scoped here).

### Phase 5.3 — a real percentage on the progress cursor ✅ shipped (2026-09-13)

Phase 5.1's fix left the cursor's progress percentage stuck at a static 50% for the whole
correspondence-build wait — the elapsed-time status text moved, but the percentage itself didn't,
which didn't fully read as "something is happening." Owner asked for the same counting-up
percentage cursor **Import Poser FBX** already shows during its own import steps.

`build_vertex_correspondence()` gained an optional `progress_callback(fraction)` argument, called
at its three real phase boundaries — after coarse alignment (~60% of the total on real Aiko3 data),
after pass 1 (~95%), and at the very end (100%) — real progress through actual measured-cost
phases, not a synthetic tick. The module stays `bpy`-free: the callback itself must not touch
`bpy`, so the operator's callback just writes a plain `self._correspondence_progress` attribute
from the background thread, and `modal()`'s timer tick (main thread) is what turns that into a real
`wm.progress_update()` call and folds the percentage into the status-bar text alongside elapsed
seconds. Verified directly against real Aiko3 data: callback fires `[0.6, 0.95, 1.0]` in order;
re-verified end-to-end (real BrowHeavy injection, `touched=2295 unmapped=0`, idempotent re-run) —
unaffected by the added instrumentation.

**Also fixed alongside:** newly applied shape keys now rest at `value = 0.0`. `shape_key_add()`
defaults a new key's value to `1.0` (fully dialed in) — harmless for one morph, but a package
applying dozens at once (Test 2) was stacking all of them at full strength simultaneously. Owner's
own words, on seeing it: "I'm assuming you set the value to 1 as a way to test that the morphs were
working?" — no, that was just Blender's own `shape_key_add()` default, never touched deliberately
until now. Morphs now rest at 0, like a Poser/DAZ dial, for the user to dial in.

### Phase 5.4 — human-readable shape-key names ✅ shipped (2026-09-13)

Injected morphs were coming in named after the CR2 channel's `internal_name` — a cryptic
sequential code like `PBMDC_39` — instead of anything a user would recognize. The channel's own
`name` property (`core/cr2/cr2_parser.py`'s existing `Channel.display_name` field) already carries
the real label; it was just never preferred over `internal_name` for the *created* shape key,
because `core/cr2/name_match.py`'s index was originally built for the opposite direction (Phase 5's
FBX-name matching problem: resolving an already-FBX-baked shape-key name, whichever of the two the
exporter happened to pick, back to its CR2 channel group).

Confirmed against real injection files in `Test_Poser_Assets`'s content library
(`InjDeltas.DC_*.pz2`): `targetGeom PBMDC_39` / `name BrowHeavy`, `targetGeom PBMDC_44` /
`name H3NsDragon`, etc. — every sampled injection-delta channel has a real label distinct from its
internal code. (Base-figure FBM channels are a different story — `PBMFullFigure` / `name
pFullFigure` is just the same p-prefix convention in both fields — so this only matters for the
injection path, not FBX-import consolidation, which is untouched.)

`core/cr2/name_match.py` gained `display_name_for(index, internal_name)`: returns the first
non-empty `display_name` found across the channel's per-actor group, falling back to
`internal_name` if none set. `operators/applyMorphInjection.py`'s `_apply_morphs()` still looks up
each channel group by `internal_name` (the CR2's real key) but names the created shape key, and
runs the JCM/already-applied checks, against the resolved display name. The already-applied check
also still checks `internal_name` — a mesh injected before this change carries the morph under its
old raw name, and re-running shouldn't create a second copy under the new one.

One real quirk this surfaced: `InjDeltas.DC_49_Mandible.pz2` declares `targetGeom PBMDC_49` twice
(once per actor) with *different* `name` values (`pMandible`, then `Mandible`) — confirms
`name_match.py`'s existing "first one seen wins if a figure is inconsistent" comment is a real,
observed case, not just defensive wording. **First-seen turned out to be the wrong tiebreak for the
shape-key name; see "Correction" below.**

**Correction (2026-11-15): the label comes from the channel with the most deltas, not the first.**
Owner UAT applied `InjDeltas.DC_40_EarDeer.pz2` and got a shape key named `pEarDeer`. The file
lists a small partial actor first (`neck`, 891 deltas, `name pEarDeer`) and the main one second
(`head`, 38,284 deltas, `name EarDeer`); first-seen picked the partial's `p`-prefixed variant.
`display_name_for()` now takes the label of the group's channel carrying the most deltas (the
morph's main body), keeping file order on ties and skipping channels with no label. Measured over
every `InjDeltas.*` in `A3-H3MorExp1/Deltas` (16 files), exactly five names change, all losing a
`p`: EarDeer, EarDragon, EarFeline, EarFin, Mandible. The other eleven were already unambiguous.
Alternatives considered: stripping a leading `p` from any name (rejected: `p` is also a legitimate
first letter, and it guesses at a convention instead of reading the data), and preferring the
non-`p` name (same objection). Meshes injected before this keep their old `pEarDeer` key; Remove
still finds it through `mesh["poser_injected_morphs"]`, which stores the key name actually created.

Verified end-to-end in headless Blender (real Aiko3 import + real BrowHeavy injection): shape key
comes in as `BrowHeavy`, not `PBMDC_39`; re-running the same injection reports it already present
(no duplicate) instead of creating a second copy.

### Phase 5.5 — browse the Poser Runtime library instead of two raw file paths ✅ shipped (2026-09-13)

`Apply Morph Injection` required hand-navigating a native OS file dialog twice — once for the
injection `.pz2`, once for the figure's reference `.obj` — every time, even though both live
somewhere inside a Runtime folder structure the owner already has organized. Investigated
`cr2_importer`'s own "Poser Library" browser (`core/runtime_index.py`, `properties/preferences.py`,
`operators/pref_ops.py`, `core/pz2_parser.py`'s `pz2_sniff`) to see what was directly reusable — its
scan/cache architecture and multi-root preferences pattern were a good fit; its full
UIList/thumbnail/background-scan browser panel was not (it exists to browse figures, props, hair,
poses, and materials all at once — more machinery than a two-field picker needs).

**New `core/cr2/runtime_index.py`** (MIT, adapted): same JSON-cache/incremental-mtime-diffing scan
as cr2_importer's version, but with two deliberate simplifications. First, no top-level
library-folder allowlist — cr2_importer only walks 10 canonical category folders and explicitly
skips vendor-prefixed folders like `!DAZ`, but that's exactly where real injection content lives
(`Runtime/libraries/!DAZ/A3-H3MorExp1/Deltas/InjDeltas.*.pz2`); this walks the whole
`Runtime/Libraries/` tree. Second, no thumbnail scanning — the picker this feeds is a text search
dropdown, not a thumbnail grid, so there's no consumer for one. Extensions narrowed to
`.cr2`/`.crz` (figures) + `.pz2`/`.p2z` (injection candidates); no content-sniffing to classify
`.pz2` files further (owner's call — real injection files are conventionally named
`InjDeltas.*`/`Inj*`, so search-as-you-type is enough, confirmed against real product libraries).
Dropping content-sniffing and thumbnails also meant dropping cr2_importer's background-thread scan
— a plain `os.walk()` (incrementally cached) is fast enough to run synchronously in `invoke()`.

**New `core/cr2/poser_library_prefs.py`** (GPL, original — no cr2_importer equivalent): Poser
itself already tracks every registered content folder in `LibraryPrefs.xml`, written/read by Poser
on every launch. Confirmed against the owner's real, live file
(`~/.wine-poser13/.../AppData/Roaming/Poser/13/LibraryPrefs.xml`, 20 registered folders) and an
older archived one (schema unchanged since at least Poser 5, 2005). `find_library_prefs_files()`
locates the *live* file via the standard per-platform/Wine preferences location (POSIX + Wine:
`~/.wine*/drive_c/users/*/AppData/Roaming/Poser/*/LibraryPrefs.xml`; native Windows:
`%APPDATA%\Poser\*\LibraryPrefs.xml`) — deliberately not a filesystem-wide search, since a stale
copy can be sitting inside old archived content (observed in the wild) and must never be mistaken
for current. `parse_content_folders()` reads every `<ContentFolder folder="...">`, strips the
trailing `Runtime/libraries` segment, and — only on POSIX, only for a drive-lettered path —
translates Wine's drive-letter convention: `Z:` → `/` (Wine's standard default mapping, verified
against the real file), any other letter (typically `C:`) resolved against the *same* Wine
prefix's own `drive_c`, derived from the XML file's own path rather than a guessed default prefix.
Anything that doesn't resolve to a real directory is silently skipped (a moved drive, removed
content, a legacy schema's bare relative path, an old file's drive letter with no Wine mapping).

**Fixed real, pre-existing dead code along the way**: `core/cr2/cr2_parser.py`'s
`_resolve_geom_file()` (now public `resolve_geom_file()`) already did exactly the resolution this
feature needs — turn a parsed `Figure.geom_file` (the `figureResFile` colon-path) into a real OBJ
path — but imported a `PoserPathResolver` from a `core/cr2/obj_loader.py` module that only exists in
`cr2_importer`, not `poser_tools`. It silently no-op'd (caught the `ImportError`, returned) because
nothing called it. Fixed to route through `poser_paths.resolve_poser_path()`, the module that
actually exists.

**`operators/applyMorphInjection.py`**: new `PoserLibraryItem` (category-qualified `name` —
`"!DAZ/A3-H3MorExp1/Deltas/InjDeltas.Foo.pz2"`, not a bare filename, since DAZ products commonly
reuse orchestrator/morph names like `"BrowHeavy.pz2"` across completely unrelated product folders,
confirmed directly: the real library has *five* distinct `BrowHeavy.pz2` files across different
vendor folders) backs two new `prop_search()` dropdowns — the same searchable-dropdown mechanism
behind Blender's own Material/Vertex Group/Shape Key pickers — for the figure's CR2 and the
injection package, populated from `runtime_index.get_all_items()` in `invoke()`. Picking a figure
resolves its reference OBJ automatically via `CR2Parser.parse_file()` +
`resolve_geom_file()` — no manual OBJ browsing at all when a Runtime root is configured. The
existing raw `injection_filepath`/`reference_obj_filepath` fields stay as a manual override
(for no-Runtime-root setups or content outside it). *Originally these won whenever filled in;
reversed 2026-11-15, see the correction below.*
The picked CR2 is remembered on the mesh (`poser_reference_cr2`, parallel to the existing
`poser_reference_obj`) so a re-run pre-fills the same figure.

**New add-on preferences** (`properties/poserToolsPreferences.py`, `operators/runtimePaths.py`):
multiple registered Runtime roots (`PoserRuntimePathItem` list, add/remove/reorder — mirrors
cr2_importer's own preferences pattern), plus a `poser.runtime_roots_import_from_poser` button that
imports roots from `LibraryPrefs.xml` in one shot instead of retyping each one. New operators use
`poser.runtime_root_*` idnames rather than cr2_importer's exact `poser.runtime_path_*` — both
add-ons share the bare `poser.*` operator namespace and could plausibly be installed side by side.

Verified end-to-end against real content (not synthetic fixtures): imported the owner's actual,
live `LibraryPrefs.xml` (20 roots resolved, including the real `Base Figures` folder); scanned
22,419 real CR2/PZ2 files across those roots; resolved a real Aiko3 CR2's `figureResFile` to its
real, correct OBJ path with zero manual input; ran a full real injection through the resolved path,
`touched=2436 unmapped=0`, matching the same pipeline's established correctness elsewhere in this
doc. `.venv/bin/python -m pytest` — 76 passed (21 new: `poser_library_prefs`, `runtime_index`, two
`resolve_geom_file` cases), including real-file tests against the owner's live install that skip
cleanly on a machine without it.

## Phase 6 — Remove Morph Injection ✅ shipped (2026-09-13)

Owner asked how a "remove injection" would work, given Poser ships a `RemDeltas.*.pz2` next to
almost every `InjDeltas.*.pz2`. Inspected a real pair directly
(`!DAZ/A3-H3MorExp1/Deltas/{Inj,Rem}Deltas.DC_39_BrowHeavy.pz2`): a Rem file re-declares the exact
same `targetGeom` channel as its Inj counterpart, with zero deltas and no `deltas{}` block at all —
Poser's own "remove" isn't a real undo, it's just overwriting the channel with nothing. The
Blender-native translation isn't "zero the shape key's data" (an inert always-0 key is just
clutter) — it's deleting the matching shape key outright, which needs none of Apply's
vertex-correspondence machinery at all: removal is pure name-matching, not geometry, so no
reference OBJ, no background thread, no progress modal.

**New `operators/removeMorphInjection.py`** (`poser.remove_morph_injection`): loads a `.pz2` via
the same `injection_package.py`/`name_match.py` pipeline Apply already uses (transparently follows
orchestrators too), reads its channel list, and removes any matching shape key from the active
mesh. Reuses the same Runtime-library `prop_search()` picker pattern from Phase 5.5 (one field, no
CR2 picker needed) plus a manual-path fallback.

**Real bug found via real content, not synthetic tests**: matching by `display_name_for()` against
the *picked* file's own channels works fine for an Inj file, but not a Rem file — a Rem file's
`name` property is always the literal placeholder `-` (confirmed: every real Rem file uses it), so
resolving names purely from whatever file the user pointed Remove at would try to match a shape key
literally called `"-"` and fail every time. Fixed by giving Apply a memory: `_apply_morphs()` now
records `internal_name -> shape-key name` for every morph it actually creates, in a JSON blob on
`mesh["poser_injected_morphs"]` (same `mesh`-not-`ShapeKey` reasoning as
`poser_shapekey_merges`/`poser_reference_obj` — `bpy.types.ShapeKey` has no ID properties). Remove
prefers this recorded name (works no matter which of the pair — Inj or Rem — the user points it
at), falling back to `display_name_for()` on the picked file itself (works when Remove is pointed
at an Inj file, or any file with real names, on a mesh with no such record yet — e.g. shape keys
that predate this change). A resolved name of literal `-` falls back to the internal name for the
report line only (`"PBMDC_39: not present"`, not `"-: not present"`) — cosmetic, doesn't change
whether anything actually gets removed. The record entry is popped once its shape key is actually
removed, so it doesn't linger pointing at a key that no longer exists.

Verified end-to-end against real content: applied real `InjDeltas.DC_39_BrowHeavy.pz2` (creates
`BrowHeavy`), removed it via the real `RemDeltas.DC_39_BrowHeavy.pz2` counterpart (shape key gone,
1/1 removed); ran Remove again on the same package (0/1, correctly reports "not present", no
error); re-applied and removed again via the *original* Inj file instead of the Rem one (also
works, confirming either file resolves correctly). No new pytest coverage needed — no new
`core/cr2/` module, purely `bpy`-dependent operator code reusing already-tested pure-Python pieces
(same manual-smoke-test convention as Apply itself).

**Known content defect: two `RemDeltas.*` files name a different channel than their `InjDeltas.*`
(found 2026-11-15, owner UAT).** `RemDeltas.DC_40_EarDeer.pz2` declares `PBMDC_40`, but
`InjDeltas.DC_40_EarDeer.pz2` declares `PBMDC_01`; `DC_42_EarFeline` likewise (`PBMDC_42` vs.
`PBMDC_02`). Remove matches by channel name, so pointing it at the Rem file reports "Not present"
and removes nothing; pointing it at the Inj file works. Scanned every Inj/Rem pair under the
owner's whole content drive: 2 of 2,305 pairs mismatch, both in `!DAZ/A3-H3MorExp1/Deltas`. Cause unknown;
probably a vendor authoring slip (I haven't checked what Poser itself does with these Rem files).
**Decision, reversed the same day: work around it.** The first call was to leave it: a sibling-lookup
fallback seemed like logic every Remove run would pay for, to paper over two broken files, and the
Inj file already worked. Owner UAT then hit it on *both* broken pairs (EarDeer, then EarFeline, the
second time after restarting Blender, so not a stale session), and the Rem file is the one Poser
users naturally pick. A workaround that needs the user to know which two of 2,305 files are broken
isn't one. Built it narrowly so ordinary runs don't pay: `injection_package.paired_injection_path()`
maps `RemDeltas.X` to the `InjDeltas.X` in the same folder (case-insensitive), and
`load_injection_package()` now also returns `leaf_paths` (the files actually parsed, so it works
through an orchestrator too). Remove uses the pair only for a Rem file **all** of whose channels
matched nothing on the mesh; the report says which file was substituted. A healthy pair never
touches the fallback (verified: BrowHeavy), and a file with no Inj sibling behaves as before.
Verified headless on the real Aiko3: applied EarFeline, EarDeer and BrowHeavy, then removed each
via its `RemDeltas.*`; all three gone, the record empty, and a repeat run reports "not present"
with no error.

## Phase 7 — Thumbnail library for picking packages (scoping, 2026-11-14; 7.0 to 7.4 done; 7.5+ not started)

**Goal.** Replace the two `prop_search()` dropdowns with a thumbnail grid for choosing injection and
removal packages from the owner's Poser Runtime libraries, using Blender's Asset Shelf. The
dropdowns stay as the fallback.

### What a feasibility pass established (all headless unless marked *owner-verified*)

- **The asset system only knows datablocks in `.blend` files.** There is no "virtual asset" for a
  `.pz2`, and Python can't add ID types: `bpy.utils.register_class` accepts a fixed list (Panel,
  UIList, Menu, Header, Operator, KeyingSetInfo, RenderEngine, AssetShelf, FileHandler,
  PropertyGroup, AddonPreferences, NodeTree, Node, NodeSocket, Gizmo, GizmoGroup), and the ID types
  are closed. So the library is a **generated index**: one stand-in datablock per Poser file.
- **Stand-in type: chosen as `Image`, changed to `Scene` (see "7.3 correction" below).** `Action` is rejected: the built-in Pose Library shelf's
  `asset_poll` is `asset.id_type == 'ACTION'`, so every stand-in would appear as a pose.
  `Mesh`/`Text` can be marked as assets but `preview_ensure()` returns `None`, so no thumbnail.
  `NodeTree` feeds Blender's modifier/node Add menus. Thumbnail-capable types, identical on
  5.0.1/5.2.4/5.3: Action, Collection (not 5.0.1), Image, Light, Material, NodeTree, Object, Scene,
  Texture, World. Image has no Python-side consumer in Blender's own scripts; its C-side drag/drop
  behavior is the part not inspectable headless (*owner-verified:* Image assets behave normally in
  the Asset Browser).
- **Thumbnails.** The Poser `.png` is set via `preview_ensure()` + `image_pixels_float` and survives
  `bpy.data.libraries.write` and a fresh-process reload, with no subprocess workaround
  (`cr2_importer`'s subprocess patch solves a different problem: saving an imported figure as an
  asset). Poser thumbnails are ~91px.
- **Scale.** 10,000 stand-ins: ~4s to build, 7 MB, <1s to read. The real 3,144-entry library:
  ~7s, 31 MB.
- **Payload.** Custom keys on `asset.asset_data` (not the ID) survive write/reload; ID-level custom
  props are unreachable from a non-local `AssetRepresentation`. `catalog_simple_name` stays blank
  when `catalog_id` is set from Python (Blender fills it only via its own catalog UI), so don't
  read it; the catalog tree itself works from `blender_assets.cats.txt`.
- **Names are not identity.** ID names are unique per `.blend`, and 1,411 of 3,144 entries (45%)
  share a name with another (`BrowHeavy` ×5, `00`/`01`/… ×11). The prototype's `~N` suffixes
  (`BrowHeavy~5`) are unacceptable as display names.
- **The shelf (*owner-verified on 5.3*).** A Python `AssetShelf` appears whenever *its own* `poll`
  passes (the pose shelf's Pose-mode rule is its own poll, not an API limit; mine shows for any
  active mesh). `wm.call_asset_shelf_popover` opens it as a popup. A single click calls
  `bl_activate_operator` with `context.asset` set; the operator received the entry name, tags and
  path. Registration also checked on 5.2.4 and 5.0.1. `template_asset_view` was removed in 5.0,
  so a grid cannot be embedded in our own popup: shelf popover or Asset Browser only.

### Prerequisite bug found: orchestrator paths are case-sensitive

`core/cr2/poser_paths.resolve_poser_path()` uses `os.path.isfile` on the literal colon-path, but
real content mixes `:Runtime:Libraries:Pose:` with on-disk `libraries/pose`. Of 1,246 `readScript`
targets that failed, **881 (71%) resolve once matched case-insensitively.** Real consequence today:
`! All Head Morphs.pz2` has 257 targets; the shipped `load_injection_package()` leaves 256
unresolved and loads zero actors, so **Apply Morph Injection applies nothing for it** (the earlier
BrowHeavy test worked only because that path happened to be all-lowercase). `cr2_importer`'s
`PoserPathResolver` had a lowercase fallback that the trimmed port dropped. This is a Linux-only
symptom on case-sensitive filesystems, and it was the first thing to fix (7.0).

**7.0 (done).** `resolve_poser_path()` now tries the exact path first (unchanged behavior), then
matches each component ignoring case and returns the on-disk casing. An optional `dir_cache`
(caller-owned) avoids re-listing folders; `load_injection_package()` creates one per load. The walk
doesn't backtrack between case-variant sibling folders (can't occur on the case-insensitive
filesystems Poser content is authored on). Real-data result: `! All Head Morphs.pz2` went from 256
of 257 targets unresolved and 0 actors to 0 unresolved, 642 actors, 259 morph names;
`All Head.pz2` has 1 unresolved, a file that is genuinely absent from the library. Seven new tests
(synthetic trees, a cache test, a loader test, and a real-library regression that skips when the
content mount is absent); the real-data test and the synthetic ones fail against the old resolver.
Also benefits `cr2_parser.resolve_geom_file()` (`figureResFile` → OBJ), which shares the resolver.

### Classification (the real risk, independent of UI)

A library entry is rarely the file that holds deltas. `INJ Base V4.pz2` is an orchestrator that
`readScript`s `InjDeltas.V4BaseAll.pz2`, itself an orchestrator over ~85 leaf files, so the rule
must **recurse** (depth-capped, cycle-guarded, case-insensitive; the loader already does this).
Prototype rule: follow `readScript` to the leaf files, sniff each (≤16 KB), then bucket:

| Bucket (3,144 thumbnailed, plain-text entries) | Count |
|---|--:|
| Injection: ≥1 leaf with real deltas, all targets resolve | 1,754 (+6 with missing targets) |
| Removal: leaves are all zero-delta / `name -` | 581 (+5 with missing targets) |
| Pose/expression: dials morph *values*, carries no deltas | 701 |
| All targets missing *from the file's own Runtime* (see correction below) | 62 |
| Injection via `.pmd` (Phase 4, unsupported) | 28 |
| Other (unexamined) | 7 |

Caveats, so nobody over-trusts the table:
- "Injection" is an upper bound. 1,285 of its 1,760 entries mix injection and removal leaves
  (980 with neutral names, e.g. `All Glute-Hip-Abs`: 38 + 38; 303 named `INJ …`; 2 named `REM …`),
  and `REM Base V4` is classified as injection because 152 of its leaves carry real deltas
  (unexplained). Leaf evidence alone can't label inj vs. rem, so name tokens (`INJ`/`REM`) should
  break ties.
- **Remove only needs channel names**, so one asset can offer both actions; the badge is a hint,
  not a gate.
- Not classified: **2,553 compressed (`.p2z`/gzip) files, 2,232 with thumbnails.** They need
  `read_poser_file()` to sniff. v1 scope is a decision (below).
- Thumbnails live on user-facing entries, not leaves: only 38 of 2,970 delta-bearing files have
  one, versus 2,369 of 4,122 orchestrators.
- **Thumbnail-less entries and `.rsr`.** Of 21,076 `.pz2`/`.p2z` files, 10,719 have no
  `.png`/`.jpg`; 1,670 of those have a same-named `.rsr`. These are **not** renamed BMPs (nor
  `.ico`, despite a leading `00 00 01 00`): all 41 sampled are classic Mac OS resource forks, each
  with a single `PICT` (QuickDraw picture, 91×91, ~21 KB). ImageMagick decodes one correctly, but
  Blender can't (it "loads" them as 0×0), and Blender's bundled Python has no PICT decoder, so
  recovering them means a small PICT v2 bitmap decoder in the add-on. Deferred; v1 uses a
  placeholder.

**Correction: most "missing" targets live in another configured runtime.** The classification
above resolved each file's targets against *its own* Runtime only, and I labelled the 62
all-missing packages "content not installed". Re-run against all 20 roots in the owner's
`LibraryPrefs.xml`, **2,872 of 2,921 missing targets (98%) are found in the `Base Figures` root**,
and 67 of the 73 flagged entries become complete. Only 6 stay incomplete: 3 are missing a single
file each (the V3/M3 head packages, all lacking `InjDeltas.HdJapanese.pz2`, a file that exists
nowhere in the library) and 3 are genuinely absent (`The Freak REM`, `claws-INJ`/`claws-REM`). The
add-on has the same blind spot: `load_injection_package()` (used by Apply *and* Remove) is never
passed the configured Runtime roots, although `resolve_geom_file()` and the pickers are, so adding
the missing runtime in Preferences would not help today. Fix = **7.0b** below. The classifier and
index must resolve against the same configured roots, so "incomplete" depends on configuration and
the index must rebuild when the roots change; the unresolved list could also point at a root from
`LibraryPrefs.xml` that isn't configured yet.

### Proposed architecture (files, not yet written)

- `core/cr2/poser_paths.py`: case-insensitive resolution with a per-directory listing cache.
  Pure Python, pytest.
- `core/cr2/library_index.py` (new, pure Python, MIT-adapted from `runtime_index.py`'s scan/cache
  idea): walk configured roots → entries `{root, rel_path, kind, status, thumb, catalog}`;
  recursion/classification above; incremental by mtime. pytest against synthetic trees plus the
  real library when present.
- `operators/buildLibraryIndex.py` (new): background thread + modal timer (same pattern as Apply),
  writes the stand-in `.blend`(s) and `blender_assets.cats.txt` under
  `bpy.utils.user_resource('CONFIG')`: **never into the Poser folders.** Registers the library
  with `asset_libraries.new(name=..., directory=...)`, keyword args only (5.3 added optional
  parameters).
- `ui/`: `POSER_AST_injections` shelf (poll: mesh active; `filter_image`; `asset_poll` on a `poser`
  tag; `NO_ASSET_DRAG`), a sidebar button for the popover, and a Rebuild button + status in the
  add-on preferences.
- `operators/applyMorphInjection.py` / `removeMorphInjection.py`: accept `(root, rel_path)` from the
  clicked asset's metadata and resolve it through the configured roots.
- Docs and `CLAUDE.md` updated per phase, as usual.

### Measured cost of applying a package (2026-11-14, Blender 5.3, Aiko3, 72,712 verts)

`! All Morphs.pz2` (A3/H3, 18 morphs): load+parse 0.3s; correspondence build **33.8s**; per morph
**~24 ms** (11 ms to build positions + 13 ms to add the shape key); whole package 34.5s. So the
"30+ seconds per morph" is really **30+ seconds per Apply *run***: the correspondence build repeats
on every run, so adding morphs one at a time pays it every time, while a whole package pays it
once. Peak RSS was 2.7 GB, almost all from the FBX import, not from applying.
Not measured: applying the 259-morph Victoria 3 head package (no V3 figure here). Its *load* is
16.4s (257 leaf files, ~64 ms each, dominated by delta parsing), and per-morph cost should scale
linearly but I haven't seen it. Implications:

- **Correspondence caching is now a real need**, not a someday item: it's what fixes one-at-a-time use.
- **A morph picker is still worth building**, for a different reason: a package can add hundreds of
  shape keys at once (clutter), and package load time grows with file count. If the picker knows
  which leaf file holds each morph, Apply can parse **only the selected leaves** (257 files → a few).

### 7.1 as built: the correspondence cache (2026-11-15)

Apply now reuses the vertex correspondence instead of rebuilding it when the inputs are unchanged.
Measured on real Aiko3 + `InjDeltas.DC_39_BrowHeavy.pz2`: cold run 33.4s (5.3) / 29.5s (5.0.1);
**warm run 0.4s / 0.5s, 63-80x faster**, with the shape-key positions bit-identical to the cold run.

Design, and where it departs from the sketch in the phasing list below:

- **Keyed on the exact inputs, not a spot check.** `core/cr2/correspondence_cache.py`
  (`correspondence_key`) is a SHA-256 over a format version, `match_epsilon`, and the bytes of the
  reference-OBJ vertices and the mesh's basis positions. An edited mesh or a different reference
  OBJ is simply a different key, so a stale map can't be applied silently; the earlier plan's "vertex
  count plus a spot hash" was weaker and no simpler. Verified: a 0.5 mm edit to one vertex forces a
  rebuild and the report says why.
- **Stored on the mesh, not on disk.** `mesh["poser_correspondence"] = {"key", "data"}`; `data` is
  an `npz` (`matched_index` as int32, `distance` as float64, the transform). It travels with the
  `.blend`, needs no cache directory, pruning or per-version config path, and a custom-property
  *group* makes the Custom Properties panel show an "Edit Value" button instead of drawing the blob.
  A save/reload round trip is exact on 5.3 and 5.0.1. Size on the real figure is 620 KB (against
  ~190 MB of shape keys once a big package is applied). `distance` stays float64 because collision
  resolution compares it; the cached result is a drop-in equal of a fresh one
  (`duplicate_targets` is rebuilt from `matched_index` by the same `find_duplicate_targets()`).
- **A disk cache is possible later, for a case this doesn't cover**: re-importing the same FBX into
  a new file. The cache key came out **identical on Blender 5.3.0 and 5.0.1**, so the FBX import is
  bit-identical across those versions and a content-keyed disk cache would hit across sessions.
  Not built: the mesh-level cache already covers repeated use of one mesh, and a disk cache adds a
  directory to manage.
- **Failure is reported, never silent.** An unreadable blob (corrupt, wrong format version, wrong
  size, out-of-range vertex index) is warned about, the correspondence is rebuilt, and the cache is
  overwritten with the good copy. `unpack_correspondence()` bounds-checks `matched_index` (it later
  indexes mesh arrays) and loads with `allow_pickle=False`: a blob can arrive in a `.blend` from
  anyone. Only the exception *type* goes in the user-facing message; numpy's own text for non-npz
  bytes suggests `allow_pickle=True`.
- The report gains a **Vertex correspondence:** line: "reused from this mesh's cache", or "built in
  Ns; cached on the mesh for next time", with the reason when a cached copy was rejected.
- Tests: 15 in `tests/test_correspondence_cache.py` (key sensitivity, exact round trip against a
  real build, unmatched points and duplicates, and every rejection path). The real-figure runs
  above are headless on 5.3.0 and 5.0.1.
- **Not verified:** the interactive path (background thread plus modal timer) can't run headless.
  It calls the same `_remember_correspondence()` from `modal()` once the thread ends; read through,
  not exercised. A manual run in the UI is the check: the first Apply shows the percentage cursor,
  a second Apply of another package on the same mesh should finish in a moment.

### Phasing (each its own atomic PR). Proposed order, pending owner OK

The cache and the picker fix today's pain and don't depend on the library, so they move ahead of it.

- **7.0** ✅ Case-insensitive resolver + tests. Fixes Apply today.
- **7.0b** ✅ Search the configured Runtime roots when loading a package: `load_injection_package(path,
  extra_roots=None)` threaded through the recursion, and Apply/Remove pass
  `get_runtime_roots(context)`. The same root list must feed the index later. Three tests (two
  synthetic, including a nested orchestrator across three roots with case differences at every hop;
  one real: `Custom Character Sets/.../!INJ Freja.pz2`, whose 2 files live under `Base Figures`) that
  fail without the change. Verified end to end on 5.3.0 and 5.0.1 with a throwaway orchestrator in
  a second root naming the real BrowHeavy leaf: no roots → cancelled with the new message; roots
  configured → applied (value 0) and removed. Also changed: a package yielding no morphs *because
  its files weren't found* now reports an ERROR naming the count, the first path and the
  Preferences setting, instead of "no targetGeom morphs found" (which pointed at the package, not
  at the configuration). The headless check monkeypatches `get_runtime_roots`; the preferences
  reader itself is unchanged and was not exercised here.
- **7.1** ✅ Correspondence cache (details below).
- **7.2** ✅ Morph picker (built 2026-11-15; GUI flow pending owner test). After a package is chosen, a dialog lists its morphs (display names via
  `display_name_for`) with checkboxes, Blender's built-in UIList filter box, select all/none; Apply
  handles only the selection and parses only the leaf files those morphs live in. Design below.
- **7.3** Library index: classifier + per-folder asset `.blend` files + registered library + a
  Preferences build button. No shelf. Plan below (scoped 2026-11-15, not built); three commits,
  one PR.
- **7.4** Built 2026-11-17 (details below): using a package from the Asset Browser (right-click,
  side panel) and from two shelves; injection opens the (7.2) dialog pre-filled, removal runs.
  Dropdowns stay for setups with no Runtime roots or no index built yet.
- **7.5** Figure shelf: pick a CR2 with a thumbnail, sets `poser_reference_cr2`.
- **7.6** Phase 5 tie-in: resolved morph names in the asset description, searchable in the browser.

### 7.2 design (reviewed and built 2026-11-15)

**Measured on the real V3/M3 "All Head Morphs" orchestrators** (warm I/O cache; cold reads from the
external drive were not measured):

| | V3 INJ | M3 INJ |
|---|--:|--:|
| leaf files / distinct morphs | 512 / 259 | 506 / 253 |
| morphs declared in 2 leaf files | 242 | 241 |
| leaf files declaring more than one morph | 0 | 0 |
| full parse as first measured (what `execute()` did) | 15.4 s | 11.8 s |
| full parse after 7.2's loader refactor (leaf files tokenized once, not twice) | 9.3 s | 7.0 s |
| scan as built (`scan_injection_package`, incl. resolving the files) | 1.1 s | 0.6 s |
| load only one morph's two files | 0.29 s | 0.20 s |

The 0.15 s / 0.10 s in the first prototype was only the regex over already-read text; the built scan
also resolves 512 `readScript` paths and reads each file twice (once to find leaves, once to scan),
so ~1 s is the honest number, about 9x cheaper than a full load rather than 100x. Merging the two
reads is available if 1 s proves too slow for opening the dialog.

Corrections to the original plan this produced:

- **A morph maps to a set of leaf files, not one.** Head morph plus partial (neck etc.) land in
  separate files in the big orchestrators. The scan returns `internal_name -> [leaf paths]`, and
  "parse only the selected" parses every file in the set, then filters the combined channel index by
  the selected internal names (so a hypothetical multi-morph leaf is still correct).
- **The scan has to read the whole file, not its head.** The label comes from the channel with the
  most deltas (Phase 5.4 correction), and that is frequently the *second* actor in the file. The scan
  therefore reads every channel's `name` and delta count and applies the same most-deltas rule (one
  shared function, `name_match.pick_display_name`). It walks the file's braces instead of tokenizing,
  which is what makes it cheap. Two details a first draft got wrong, both found by diffing against
  the parser on real files rather than on the three packages I started with:
  - The delta count is the channel's **`indexes`** (the number of `d` lines the parser keeps), not
    `numbDeltas`, which is the owning actor's vertex count (38,284 on every head channel). The first
    draft used `numbDeltas` and happened to pass because the head actor outnumbers the neck. A binary
    (`.pmd`) morph reports 0, as the parser does: its `indexes` counts deltas that aren't in the file.
  - Channels are only the ones the parser keeps: a `targetGeom` directly inside `actor`/`prop` ->
    `channels`. Pose files also contain `targetGeom` keyframe blocks under `ikchannels` or outside any
    actor; a first draft listed 45 of 1,170 sampled files differently because of those, plus names
    with spaces (the parser keeps the rest of the line and collapses repeated spaces), plus
    `keys { ... }` blocks nested ahead of `deltas` that defeat a flat pattern.
- **Scan and parser are two readers of one format**, so divergence is the risk. The scan only builds
  the list: Apply still parses with the real parser, so a scan miss hides a morph, it can't apply
  wrong data. Tests assert scan == full parse on synthetic cases (nested `keys`, spaces in names,
  `ikchannels`/stray blocks, binary morph, CRLF, quotes, two files per morph) and, skipped when the
  drive is absent, on three real 253-259-morph orchestrators. Measured separately across the whole
  content drive: 4,658 leaf files / 70,690 channels, one file differs, a pose file
  (`Legacy Animals/Horse/00.pz2`) where the parser merges 6 channels the file declares twice. Pose
  files aren't packages anyone applies, and Apply must report a selected morph it can't find rather
  than skip it silently, so that residual is accepted. Scan 3.4 s vs. parse 26.4 s on that sample.
- **The existing 11-15 s main-thread freeze is a separate problem.** Parsing the package happens in
  `execute()` before the modal starts, so a big package freezes Blender with no feedback. 7.2 shrinks
  it for partial selections but Select All brings it back. Moving the parse into the worker thread
  is a follow-up, not part of 7.2 (decision 10 below).

**Flow.** The package is picked *inside* the current popup, so the scan can't run at `invoke`: two
dialogs. Dialog 1 (figure + package, as today) scans on OK and opens dialog 2 (the morph list), whose
OK runs the existing modal apply with the selected internal names. The 7.4 shelf opens dialog 2
directly. A package with a single morph skips dialog 2 (decision 9).

**Row statuses:** new; already present (unchecked, disabled); JCM (skipped, disabled); no deltas
(every morph of a `RemDeltas.*` package); binary morph (`.pmd`, unsupported, disabled). Header line
"Apply N of M" with a time and memory estimate. Each shape key stores `co` as 3 float32 (12 bytes
per vertex), so ~0.87 MB on a 72,712-vertex mesh, ~226 MB for all 259 V3 head morphs (arithmetic,
not measured). Select all/none act on every row: the UIList filter text lives in the UI list's own
state, not on the operator, so "select all that match the filter" isn't available to `execute()`.

**Unverified, needs a GUI spike (dialogs can't run headless):** checkboxes in a `template_list`
inside `invoke_props_dialog`; whether `update=` callbacks fire for operator properties (decides
whether Select all/none can be a toggle); chaining dialog 1 into dialog 2 with `INVOKE_DEFAULT` from
`execute`; per-row state surviving a filter change. The spike script is throwaway, not committed.

**As built.** `poser.apply_morph_injection` keeps its id and its scripted behaviour (no selection =
every morph). Three hidden `SKIP_SAVE` properties carry the hand-over: `use_morph_picker` (set only
by the interactive `invoke()`, so dialog 1's OK diverts to the picker while a scripted call doesn't),
and `selected_morphs`/`selected_leaves` (JSON; when set, `invoke()` skips dialog 1 and runs).
`poser.pick_injection_morphs` (`INTERNAL`, so not in F3 search) scans in its `invoke()`, classifies
(`classify_morphs`), applies directly when the package holds exactly one morph, cancels with a message when
nothing is choosable, and otherwise opens the list (even for one choosable morph among many, so
the greyed rows show why the rest were left out). Select all/none are boolean toggles with `update=` callbacks
that reset themselves (spike-verified); they act on every unlocked row, not on the filtered ones.
Apply reports a chosen morph the parser can't find (the scan/parser residual above) instead of
dropping it.

Verified headless on 5.3.0, 5.2.4 and 5.0.1 against real Aiko3: choosing 2 of 3 morphs from a throwaway
orchestrator applies exactly those two (`BrowHeavy`, `H3NsDragon`) from only their files; a chosen
morph absent from the files is reported and the rest applied; a scripted call with no selection still
applies all three (`EarDeer` correctly labelled); all new classes register and re-register cleanly.
**Not verifiable headless:** under `--background`, `INVOKE_DEFAULT` runs `execute()` (checked with a
bare probe operator), so the dialog chain (dialog 1 -> picker -> modal Apply, nested `bpy.ops` calls)
needs the GUI. The one nesting not spike-tested is a *modal* operator started from inside another
operator's `invoke()`/`execute()`; if Blender refuses it, the fallback is to start the modal Apply from
a `bpy.app.timers` callback, at the cost of losing `context.window`.

**Correction to Phase 5.5's precedence: a pick from the lists now wins over the path fields (owner
UAT, 2026-11-15).** "Manual path wins if filled" was right for a setup with no Runtime roots, and
wrong once the lists existed: Blender remembers an operator's last-used property values, Apply writes
the picked package into `injection_filepath` when it runs, so the next dialog opened with the previous
package pre-filled and silently ignored a different pick. The figure picker had the same flaw (the
remembered reference `.obj` was pre-filled next to the pre-selected remembered CR2, so choosing another
figure was ignored). Now: the lists start empty every time, so a value in them is a pick made in this
dialog, and it wins; `injection_filepath` and the two list names are `SKIP_SAVE`; when the remembered
figure is pre-selected its `.obj` is left blank and resolved from the CR2; the dialog says "this path is
ignored" if both are filled; a pick that matches no entry falls back to a filled path (scripted
callers), and the picker hand-off (`selected_morphs` set) ignores list names entirely because its paths
are already settled. Remove had the same pattern and the same fix. Verified headless on a real Aiko3
with the library items passed as collection properties: stale path + other pick -> the pick is
applied; path only; unknown pick + path; unknown pick alone -> the error; hand-off with a stale name;
Remove with a stale path + other pick. Not verified in the GUI.

**Reports from a nested operator never reach the UI (owner UAT, 2026-11-15).** Step 6 of the PR's
test plan: re-applying an already-applied one-morph package should say "Nothing to apply", and
nothing appeared. The picker is started with `bpy.ops` from Apply's `execute()`; an operator called
from Python keeps its own reports off the UI (WARNING/INFO are not shown anywhere; ERROR becomes a
`RuntimeError` in the caller). I believe that is the cause but did not confirm it in the GUI; the fix
doesn't depend on it. `applyMorphInjection.say()` records every report these operators make, and
`call_nested()` runs the inner operator, tolerates the `RuntimeError` only when the inner one
reported something (any other `RuntimeError` is a real failure and still raises), and re-issues the
recorded report from the UI-invoked outer operator. The record is left set so a doubly nested call
(Apply -> picker -> Apply) forwards the innermost message. Verified headless with probe operators
(warning, error, real exception, double nesting, no stale leak). The single-morph path hit the same
thing: its "Applied 1/1" summary was swallowed whenever the cache made Apply finish synchronously
inside the nested call. The same trap awaits the 7.4 shelf, whose click will also start Apply.

**Morphs the FBX already carries (owner question, 2026-11-15).** Checked against the real
`Aiko3 SP All Morphs.fbx`, which has Stephanie Petite's third-party morphs baked in (1,194 raw keys,
321 after consolidation). Baked keys are named by the channel's **label**, not its internal code
(`Barbarian`, `BrstNatural`; consolidation strips the `p` from keys it promotes), so the label
comparison Apply always did works for most of them: StephPetite Head, 111 of 111 detected, on both the
consolidated and the raw import. **One real gap:** a package's main channel is often labelled with the
partial marker (`pBarbarian`), which the consolidated mesh no longer has, so StephPetite Body read as
48 present / 47 new, of which 46 were `p`-labelled duplicates of keys already on the mesh (1 genuinely
new: `pRubenesque`, absent in every form). Fix: `name_match.present_name_candidates()` adds the bare
form of a `p`+capital label to the names checked, used by both the picker's classification and Apply's
skip. End to end on that FBX: the 95-morph package now adds 1 key and skips 94 (before: ~47 added).
Across the whole `!DAZ` library 289 of 1,953 morphs carry a `p` main label.
Not solved, by design of a name check: **the same label on a genuinely different morph** reads as
"already present" and is skipped; telling them apart needs comparing geometry (a correspondence build
plus a delta comparison per morph), not built.
**Decided (owner, 2026-11-15): new keys keep the package's own label.** A standalone `pRubenesque` is
added under that name even though consolidation would have stripped the `p` from the equivalent baked
key. Reason: the key then matches what the package says (and what Remove's record stores), and
renaming Apply's output would be a naming change nobody asked for. The cost is the two spellings
coexisting on one mesh; `present_name_candidates` already treats them as the same morph, so it can't
produce a duplicate.

**Not in 7.2:** Remove stays name-based and unchanged (the list module should be reusable by it
later, "remove the selected morphs"); a disk cache; off-thread parsing.

### 7.3 plan: the library index (scoped 2026-11-15; 7.3a, 7.3b and 7.3c built)

**What 7.3 delivers.** A built, registered Blender asset library holding one stand-in `Scene` asset
per injection or removal package in the owner's Runtime roots, each with its Poser thumbnail (or a
placeholder), tags, a catalog (the folder tree), and the payload the click will need. Verified
headless and browsable in Blender's Asset Browser. **Not** in 7.3: the shelf and its click (7.4),
the figure shelf (7.5), morph-name search (7.6); Apply and Remove are untouched.

#### 7.3 correction (owner GUI test, 2026-11-16): two mistakes, both found by looking

The first GUI test of 7.3c showed the library registered, its catalogs listed, and **"No items"**.
Everything the headless checks could see was right (Blender's own cache,
`~/.cache/blender/asset-library-indices`, held all 139 files and 3,956 entries with the right catalog
ids, tags and payload keys), so the question was what the Asset Browser does with them. I drove a real
Blender window (throwaway config folder, screenshots) instead of guessing.

**1. The Asset Browser does not list `Image` assets.** Built a test library with 10 stand-in types:
the browser listed exactly 7 (Action, Collection, Material, Node Tree, Object, Scene, World); Image,
Light and Texture were invisible, with every type filter on and with every one off. Blender's own
Essentials library filled the same window, so the capture was valid, and the earlier prototype
library (also `Image`) showed "No items" too: it had only ever been seen in the Asset *Shelf*
(whose type filters include Image). The feasibility pass verified listing through
`bpy.data.libraries.load()` and the shelf and never opened the Asset Browser, yet the plan said
"browsable in the Asset Browser". That claim was untested.

Choice of type, among those the browser lists: **Action** is taken wholesale by the Pose Library shelf;
**NodeTree** feeds the Add menus; **Collection** has no thumbnail on 5.0.1; **Object**, **World** and
**Material** are drag-and-drop targets (a dropped stand-in adds an empty, replaces the world, or assigns
a blank material). **Scene** has no drop target and no other consumer I know of. Cost is not a factor:
4,000 stand-ins are 0.4 MB (Object), 0.5 (Image), 1.1 (World), 2.1 (Scene), 4.4 (Material). The Asset
Shelf has a filter for every type (`filter_scene` among them), so 7.4 is unaffected. Stand-ins are now
`Scene`; `ASSET_FORMAT_VERSION` is 2, so existing indexes are rebuilt.

**2. 154 visibility files were still on the removal list.** 7.3a's fix for `Hide.*` files handled
`valueParm ... name -`. The Asset Browser's All view then showed rows of `Hide.*` / `*_Hide` removal
tiles: the other flavour, `targetGeom X { name - hidden 1 }` with no delta list (La Roo `ChanVis`
`H_*_Hide.p2z`, `Hide.*` in several vendors' folders), still matched "`name -` and no deltas". A first
attempt at a fix, "`hidden` means not a removal", was wrong too: a vendor's real
`*.RemDeltas.pz2` (HiroGen) also carries `hidden 1`, and 14 of those plus the 15 `* REM.pz2` entries
that read them dropped out. The test that caught it compared what had moved against what it should
have. What separates them: **a removal re-declares the channel's delta list, empty** (`indexes 0`,
`numbDeltas N`, `deltas { }`); a visibility file declares none. `ScannedChannel.declares_deltas`
carries it, and `sniff_leaf` requires it. A third group surfaced in the same comparison: La Femme's
`REM ... Morphs.pz2` are lists of `removePMDFileMorphs`/`removeFullBodyMorph` commands, the binary-morph
system's removal, so they are now `pmd` (unsupported, hidden) rather than removal or pose.
`MANIFEST_VERSION` is 2: entry answers are cached by file stamps, so a rule change has to invalidate
them explicitly.

**Final numbers on the owner's 20 roots** (the tables below are the first run and are kept as measured):
injection 2,135, **removal 1,681** (was 1,821), pose 4,721, other 7,700, `.pmd` 141 (was 83), missing
27; **3,816 shown in 133 folders** (was 3,956 in 139); thumbnails 2,327 image / 1,458 `.rsr`-only / **31
none** (was 156: the visibility files had no images); catalogs 144. Rebuilt and read back on Blender
5.3.0, 5.2.4 and 5.0.1: 3,816 assets, 2,327 real thumbnails and 1,489 placeholders, 0 failures, 0
mismatches, 133 files / 28.3 to 28.6 MB, first build 3.5 to 5.2 s, second build writes nothing. In the
real Asset Browser (5.3): the catalog tree, thumbnails, the placeholder tiles, the folder shown on
repeated names (`BrowHeavy (!A3-H3 MORExp1 INJ)`), and a folder view all render, and the `Hide.*` rows
are gone from the All view.

#### What the real library looks like (first run, 2026-11-15, 20 roots; superseded by the final numbers above)

| | |
|---|--:|
| CR2/CRZ/PZ2/P2Z files found by `runtime_index` (walk time) | 22,419 (0.3 s) |
| package-format files (`.pz2`/`.p2z`) | 21,076 |
| of those, `Deltas` leaves (`InjDeltas.`/`RemDeltas.`/in a `Deltas` folder): never entries | 4,671 |
| user-facing entries | 16,405 |
| classified **injection** (1 of them incomplete) | 2,135 |
| classified **removal** | 1,821 |
| `.pmd` (unsupported, hidden) / all targets missing (hidden) | 83 / 27 |
| poses and expressions (hidden, decision 1) | 4,627 |
| other: props, hair, materials, anything with no morph channels | 7,712 |
| **shown (injection + removal)** | **3,956 in 139 source folders** (median 28 per folder, max 179) |
| classification time, 16,405 entries, 17,117 distinct leaf files read (first 16 KB each), warm cache | 8 s |

Shown entries by image: injection 1,745 `.png`/`.jpg`, 373 only `.rsr`, 17 none; removal 597 / 1,082 /
142. Compressed (`.p2z`) shown entries: 22, all removal.

#### Findings that change the plan

1. **A removal rule from the prototype was wrong, and would have put 624 bogus tiles on the Removal
   shelf.** The prototype called a file a removal if it had a `name -` anywhere. `Hide.*` /
   `Unhide.*` files (Victoria 3 `ChanVis` folders: channel-visibility toggles) have `name -` on a
   `valueParm`, not a `targetGeom`. The classifier must parse channels (`injection_scan.scan_channels`
   on the 16 KB head), not grep. Corrected, removal is 1,821, not 2,600; Hide/Unhide and 155 others
   fall to "pose/expression" and are hidden. Found by diffing the cheap sniff against a full parse on
   a sample, not by looking at the totals.
2. **The cheap sniff is trustworthy for what is shown.** On 4,000 random leaf files, sniff vs. full
   parse agree on every injection (485), removal (444) and `.pmd` (17) file; the only differences are
   213 pose files the sniff calls "other" instead of "morph values" (both hidden) and one `.pmd` whose
   first `targetGeom` is past 16 KB (hidden either way). Residual risk: an injection leaf whose first
   `targetGeom` is beyond 16 KB would be hidden, not mis-shown; 0 seen in 4,000.
3. **Same-folder names never collide, so one `.blend` per source folder works.** 0 of 3,956 entries
   share a stem with another in the same folder. Across folders, repeats are the norm: 71% of shown
   entries share a name with another, 60% of injection and 66% of removal *within their own kind*
   (max 5). Adding the parent folder name leaves 1 identical pair per kind. See decision B.
4. **Only 1 injection entry is incomplete** with all 20 roots configured (`!V3 All Morphs INJ/All
   Head.pz2`, 1 of 72 files missing: the known `HdJapanese`). The earlier 73 was an artifact of
   resolving against each entry's own Runtime, and the census script's second one (`! All Head
   Morphs.pz2`) was a regex miscount: the tokenizer, which Apply uses, resolves all 512 of its files.
   Decision 5's dimmed thumbnail with a red frame, for one entry, is not worth image compositing.
   See decision E.
5. **`.rsr` matters more than the earlier "deferred" suggested.** 1,455 of 3,956 shown entries (37%)
   have only a Mac-resource-fork `.rsr` thumbnail: 59% of removal, 17% of injection. Without a PICT
   decoder those get a placeholder. See decision D.
6. **Deferring compressed entries costs almost nothing** (22 shown, all removal) and the classifier
   handles them anyway (`gzip` head read), so decision 7 stands with no extra work either way.

#### Design

**Entry model.** An entry is a user-facing `.pz2`/`.p2z` that is not a `Deltas` leaf, classified by
following `readScript` (case-insensitive, all configured roots, depth-capped, cycle-guarded: the same
resolver Apply uses) to its leaf files and sniffing each:

| leaf says | rule (first 16 KB) |
|---|---|
| `.pmd` | `injectPMDFileMorphs`, or a channel with `useBinaryMorph 1` |
| injection | a `targetGeom` with `indexes` > 0 and a `deltas` block |
| removal | a `targetGeom` channel (parsed, not grepped) with `name -` and no deltas |
| pose/expression | `targetGeom` present, none of the above |
| other | no `targetGeom` |

An entry is **injection** if any leaf is, else `.pmd` if any is, else **removal** if any is, else
hidden. All targets missing: hidden. Some targets missing: shown, tagged `incomplete`. Mixed
injection/removal entries (the caveat in the classification section) are tagged `injection` and may
also carry `removal`; the content, not the name, decides.

**Modules.**
- `core/cr2/library_index.py` (new, pure Python, MIT-adapted from `runtime_index.py`'s scan and
  cache; the classifier is original, so GPL header on the new parts, decide per file as usual):
  `classify_entry()`, `find_thumbnail()` (same stem, `.png`/`.jpg`/`.jpeg`, case-insensitive),
  `build_manifest()`. Reuses `runtime_index.get_all_items()` for the file list and
  `resolve_package_files()` / `scan_channels()` from 7.2.
- `core/library_assets_child.py` (moved out of `core/cr2/`, which stays free of `bpy`): a **stand-alone
  script** with no add-on imports, run by a child Blender process (below). Writes the `.blend` files.
  `core/library_build.py` is the parent side: planning, the process, the catalog file, registration.
- `operators/buildLibraryIndex.py`: `poser.build_library_index`, a modal operator.
- `properties/poserToolsPreferences.py` `draw()`: **Build / Rebuild Library Index**, a status line
  (N entries, built when, "roots changed, rebuild"), and **Remove Library Entry**.

**Manifest** (`library/manifest.json` next to the `.blend` files): a `signature` (format version,
ordered root list, Blender version) and per source folder `{path, hash, entries:[{rel_path, root,
stem, ext, kind, incomplete, thumbnail, thumbnail_mtime, leaf_stamps}]}`. `leaf_stamps` are the
(path, mtime) of the leaves an entry resolved to, so a changed leaf re-classifies its entry although
the entry file itself did not change; checking them is ~17,000 `stat` calls. A folder's `.blend` is
rewritten only if its hash changed; a different signature rebuilds everything.

**Where it lives.** `bpy.utils.user_resource('CONFIG', path='poser_tools')/library/`, the same place
`runtime_index` already keeps its cache, **never inside the Poser content folders** (it is the
owner's library, the index is derived data). Per Blender version, because that directory is. A
snapshot of the roots' file mtimes before and after a build must be identical (a test).

**Build process.** Classification and manifest on a background thread of the main process (pure
Python, same pattern as Apply's correspondence build: modal timer, percentage on the cursor and
status bar, Esc cancels). Then **one child Blender process**
(`bpy.app.binary_path --background --factory-startup --python library_assets_child.py -- <manifest>`)
creates the stand-ins and writes the `.blend` files. Reasons it is not done in the session:
`bpy.data.libraries.write` needs the datablocks to exist, so building ~4,000 asset-marked stand-ins in
the user's session would add and remove them from the file the user is working in (they would flash
through "Current File" assets, touch the undo stack and the saved file), and a frozen UI for ~10 s
unless sliced. The child leaves the session untouched, is exactly what the prototype already did
(background scripts), and has precedent in `cr2_importer`'s own subprocess save. Verified that it
works: a Blender 5.3.0, 5.2.4 LTS and 5.0.1 session each launched a child from `bpy.app.binary_path`
that wrote an asset `.blend` (0.4 to 2.2 s overhead). Not verified: Windows/macOS, a flatpak where
`binary_path` may differ.

**Stand-ins** (headless-verified on the three versions; the type itself was corrected after the owner's
GUI test, see "7.3 correction"): `Scene` (first `Image`, which the Asset Browser doesn't list), marked as an
asset, thumbnail via `preview_ensure()` + `image_pixels_float` from the Poser image. Tags: kind
(`injection`/`removal`), `incomplete`, `poser`. Catalog: the folder tree below `Libraries/`
(`uuid5` of the path, so ids are stable across rebuilds). Description: the path relative to its root
(shown as the tooltip). Custom keys on `asset_data`: `poser_root`, `poser_rel_path`, `poser_kind`.

**Registering the library.** `preferences.filepaths.asset_libraries.new(name=, directory=)`, keyword
arguments only (5.3 added optional ones), **after checking for an existing entry**: probed on 5.3.0
and 5.0.1, calling `new()` twice with one name silently makes `Name.001`. Update `path` if the entry
exists. `enabled` and `is_available` exist on 5.3 only (`hasattr`). Removing it is an explicit
Preferences button; `unregister()` leaves it, because unregister also runs on every script reload.

#### 7.3a as built (2026-11-15)

`core/cr2/library_index.py` (pure Python, GPL) and `runtime_index.walk_roots()` (a no-cache walk that
also says which root a file came from; the existing `get_all_items()` writes its cache through `bpy`,
so pytest can't call it, and the walk is 0.3 s anyway). Main entry points:

- `classify_entry(path, roots)`: follows `readScript` (same tokenizer and case-insensitive resolver as
  Apply), sniffs each leaf's first 16 KB, returns `kind` (`injection`/`removal`/`pmd`/`pose`/
  `missing`/`other`), `incomplete`, `mixed`, file counts, and `stamps` (path, mtime, size of every
  file read). Depth is capped at 8 and cycle-guarded; hitting the cap marks the entry incomplete.
- `build_manifest(roots, previous=None, ...)`: classifies every entry (reusing any whose stamps are
  all unchanged), finds thumbnails, assigns names/catalogs/tags/descriptions, groups the shown
  entries per source folder with a content hash, and returns `{classified, folders, stats}`.
  `changed_folders(previous, manifest)` says which folder files to write or remove.
  `save_manifest`/`load_manifest` persist it gzipped (12.6 MB as JSON, 0.6 MB compressed), with each
  repeated path stored once.
- `prefetch_heads()` for the cold-drive case above; `BuildCancelled` for the modal operator's Esc;
  `progress(phase, done, total)` with phases `reading` and `classifying`.

**Verified on the real library (20 roots), matching the census to the entry:** 16,405 entries;
injection 2,135, removal 1,821, pose 4,627, other 7,712, `.pmd` 83, missing 27; **3,956 shown in 139
folders**; thumbnails 2,342 image / 1,458 `.rsr`-only / 156 none (the census had 1,455 `.rsr` and 159 none:
3 `.rsr` files differ from their entry only in letter case, e.g. `LipsThin` / `Lipsthin.rsr`, which a
case-sensitive stem match missed and `find_thumbnail` finds); the longest
display name is 42 bytes (limit 63); 0 mixed injection/removal entries. Full build 9.5 s warm, 40 s
cold-contents; rebuild from the saved manifest 0.7 s with all 16,405 entries reused and no folder
needing a rewrite; the mtime of every file under the roots is unchanged by a build.

**Tests** (`tests/test_library_index.py`, 42): the leaf sniff including the Hide/Unhide trap, nested
orchestrators, case-insensitive targets, targets in another root, missing targets, cycles and depth,
`.p2z` (and a `.p2z` that isn't gzip), mixed entries, stamps, thumbnails, names and the 63-byte limit,
incremental reuse (touching one leaf re-classifies only the entry that read it), a changed root or
format discarding the cache, `changed_folders`, cancel and progress, persistence (corrupt, truncated,
wrong-format files load as `None`), nothing written under the roots; plus real-library checks that skip
without the drive (known entries, the Hide files, the walk finding exactly the leaves Apply finds, and
600 sampled leaves where the sniff must agree with a full parse on every injection and removal).
Two of them were mutation-checked: reintroducing the `name -` grep fails 6 tests, and making
`_stamps_current` always false fails 3.

#### 7.3b as built (2026-11-15)

**Files.** `core/library_assets_child.py` (runs in the child Blender; stdlib, `bpy`, `numpy` only) and
`core/library_build.py` (no module-level `bpy`, so it is pytest-able). Small additions to
`library_index.py`: `catalog_uuid`/`catalog_ids`/`catalog_file_text`, `ASSET_FORMAT_VERSION` and
`asset_signature(blender_version)`, which goes into the manifest signature so a change to what the
asset files contain, or a different Blender, rebuilds them.

**Division of labour.** The parent plans (`plan_asset_build`: folders whose hash changed, folders whose
`.blend` is missing from disk, strays and `.tmp-` leftovers to sweep), writes the job JSON, runs the
child (`run_child`: output read on a thread so a silent child can still be cancelled, which terminates
then kills it), writes the catalog file, deletes stale files, and saves the manifest **last**, so a
failed or cancelled build leaves the previous baseline and is just redone. The child only builds
datablocks and writes one `.blend` per folder (to a `.tmp-` name, then renamed, so an interrupted build
never leaves a half-written file under a name the library scans). Protocol on stdout: `POSER_PROGRESS
n total`, `POSER_DONE {json}`, `POSER_ERROR msg`; `--python-exit-code 1` makes an uncaught exception
fail the build.

**Stand-ins.** `Scene`, asset-marked, preview from the Poser thumbnail via numpy
`foreach_get`/`foreach_set` (previews over 256 px are scaled down; 19 of the real ones were), tags,
catalog id, description (the path, with the `[INCOMPLETE ...]` prefix where it applies), and custom keys
`poser_root`, `poser_rel_path`, `poser_kind` on `asset_data`. **Placeholders** (decision D): a flat
128 px tile by kind, blue with a plus for injection and amber with a minus for removal, so the two read
apart without colour; no text (Blender can't draw it onto an image in a background process, and the
shelf prints the name). A thumbnail that fails to load falls back to the placeholder and is counted.

**Catalogs.** Written by the parent, one line per folder in the tree and every parent, with
`uuid5` ids of the path so they are stable across rebuilds.

**Registration.** `ensure_asset_library(libraries, name, directory)` reuses an existing entry and only
corrects its path (and re-enables it on 5.3, where `enabled` exists), because `new()` twice with one
name silently makes `Name.001`; `remove_asset_library`. Written against the collection so it is tested
with fakes. Called by the 7.3c operator, only from the explicit build click.

**Verified with the real child on the real library, Blender 5.3.0, 5.2.4 LTS and 5.0.1** (the parent
built the manifest from the 20 roots and ran the child; a separate process then read every file back):

| | 5.3.0 | 5.2.4 | 5.0.1 |
|---|--:|--:|--:|
| first build (child alone) | 5.0 s (2.4 s) | 5.3 s (2.4 s) | 3.5 s (2.3 s) |
| second build, nothing changed | 1.3 s, child not run | 1.1 s | 1.3 s |
| assets / files / size | 3,956 / 141 / 28.9 MB | 28.8 MB | 28.6 MB |
| thumbnails / placeholders / failed | 2,342 / 1,614 / 0 | same | same |

(plus ~10 s for the manifest on a warm cache, see 7.3a). Read-back, on all three: **0 mismatches in
3,956 assets** across names, descriptions, tags, catalog ids (and that each is the stable uuid5 of its
path), the three payload keys, and previews: 2,323 thumbnail previews are pixel-identical to their source
images (max difference 0.0000), the 19 larger than 256 px are scaled to 256 with their aspect ratio
kept, and every placeholder is 128 px. All 157 catalogs the entries use are in the catalog file.
Deleting one asset file and rebuilding rewrote exactly that one. The registration call created the
library then left it unchanged on a second call (one entry, not `.001`), and the content roots'
file mtimes were identical before and after. With the real binary, a corrupt or missing thumbnail
falls back to a placeholder (counted as failed), and an unwritable output directory or a malformed job
file fails with a non-zero exit and a `POSER_ERROR` line that `run_child` reports.

**Tests** (`tests/test_library_build.py`, 19; 3 more in `test_library_index.py`; fixtures shared in
`tests/synthetic_library.py`): plan (fresh, up to date, a deleted file rewritten, strays and `.tmp-`
leftovers swept), the command line, `run_child` against a stand-in child speaking the same protocol
(progress, summary, a malformed progress line, error, crash, silent exit, cancelling a hung child,
a binary that can't start), `build_assets` (files, catalogs, manifest saved last, a second build never
starts the child, only a changed folder rewritten and a vanished one removed, a failed build saves
nothing, a cancelled one raises `BuildCancelled`), registration with fakes (idempotent, path corrected,
disabled re-enabled, removal touches only its own entry), and catalog ids/text and the asset signature.

**Not verified:** the Asset Browser actually showing the library (needs the GUI; 7.3c's Preferences
button is the way to try it), Windows/macOS, and a flatpak where `bpy.app.binary_path` may differ.
`bpy.data.libraries.load()` is how the files were read back; the shelf reads the same data through
`AssetRepresentation`, which 7.4 checks.

#### 7.3c as built (2026-11-15)

**`poser.build_library_index`** (`operators/libraryIndex.py`, `INTERNAL`, polls false with no Runtime
folders or while a build runs): the same shape as Apply's correspondence build. `_work(job)` does the
whole thing (load the previous manifest, `build_manifest`, `build_assets`, write `status.json`) and
never touches `bpy`; interactively it runs on a thread while a `modal()` timer (0.2 s) maps
`(phase, done, total)` onto one bar (`library_build.progress_fraction`: reading 0 to 55%, classifying
55 to 85%, writing assets 85 to 100%, the bands taken from the measured cost split) and drives the wait
cursor, `wm.progress_update` and a status-bar line naming the phase. **Esc** sets a flag the worker
checks between files and that terminates the child Blender; the modal waits for the thread, so it can't
outlive the operator, and nothing is changed (the manifest is saved last). Under `--background` it runs
`_work` inline, as Apply does. On the main thread afterwards: `ensure_asset_library(...)` (the only
place preferences are edited, and only from this click), the **Poser Library Index Report** text block
(`library_build.report_lines`: counts, thumbnails, files written/unchanged/removed, the child's summary,
incomplete packages), and an INFO message. An error from the child or any exception is reported and
written to the same block, nothing is registered, and the previous index stays as it was.
`Full Rebuild` is the same operator with `force`, which ignores the previous manifest.

**`poser.remove_library_entry`** removes the registered library and leaves the files; polls false when
there is no entry.

**Preferences** (`_draw_library`, below the Runtime folders): package and folder counts, built-at,
thumbnail/placeholder counts, an alert row when stale (roots, asset format or Blender version differ from
the last build, from `library_build.status_is_stale`), Build/Update and Full Rebuild buttons, and the
registered state with the remove button. It reads only `status.json` (cached on its mtime), never the
manifest, because Preferences redraws constantly.

**Verified headless on Blender 5.3.0, 5.2.4 and 5.0.1, on the real library** (scripted calls with
`library_dir` redirected to scratch): first build 13 to 15 s end to end (manifest warm ~10 s, child ~5 s),
3,956 shown in 139 folders, library registered, status not stale; an update with nothing changed 1.3 to
1.4 s with 139 files unchanged and none written; a full rebuild rewrites all 139; the status goes stale
when a root is dropped; remove-entry unregisters and keeps the files, and refuses a second time (poll);
no roots refuses (poll); a child that can't start reports "couldn't start ...", writes the failure
into the report block, and registers nothing. The Preferences draw code was driven with a recording
layout through the four states (never built with no roots; built and current but unregistered; registered;
stale) and produced the expected labels, buttons and alert.

**Not verified:** the modal wrapper itself (timer, cursor, status bar, Esc), which needs the GUI, and the
Asset Browser actually listing the library. `tests/test_library_build.py` covers the pure pieces added
here (status read/write/cache/damage, staleness, the progress mapping, the report).

#### Verification

- **pytest** (no `bpy`): `classify_entry` on synthetic trees: orchestrator depth and cycles,
  case-insensitive targets, targets in another root, missing targets, `.p2z`, `.pmd`, a `Hide.*` file
  (the trap), mixed injection/removal; `find_thumbnail`; manifest incrementality (touch one leaf
  re-classifies its entry only; a new root changes the signature); the naming rule. Skipped-when-
  absent real-library tests: sniff vs. `scan_channels` agreement on a sample, and the corpus counts
  above within a tolerance (they will drift as content changes, so assert shape, not exact numbers).
- **Headless Blender on 5.0.1 / 5.2.4 / 5.3.0:** build the real index; reopen the library; assert
  entry count, previews present, custom keys readable via `bpy.data.libraries.load(assets_only=True)`,
  tags and catalogs; a second build rewrites nothing; touching one folder's leaf rewrites only that
  folder's `.blend`; registration is idempotent; the roots' mtimes are unchanged.
- **Owner, GUI:** build from Preferences, open the Asset Browser, see the library and its folder
  tree with thumbnails; rebuild after changing the roots.

#### Phasing (one PR, three commits)

- **7.3a** ✅ `library_index.py` + manifest + tests. Pure Python, the part that must be right. As built below.
- **7.3b** ✅ `library_assets_child.py` + the subprocess driver + registration, verified headless. As built below.
- **7.3c** ✅ the operator and Preferences UI, docs (README, CHANGELOG, `CLAUDE.md`, this doc). As built below.

#### Decisions for the owner (recommendation first)

- **A. Build in a child Blender process, not in the session.** Recommended, reasons above. The cost is
  a dependency on `bpy.app.binary_path` working (tested on the Linux builds here).
- **B. Name display.** Keep the file stem as the asset name; add the parent folder only to names that
  repeat *within their kind* (`BrowHeavy (A3-H3 MORExp1)`, truncated to fit Blender's 63-byte ID
  limit; longest stem today is 31). 60 to 66% of entries repeat, so plain names (doc option (a))
  leave most of the grid ambiguous, though thumbnails differ per product. The suffix depends on
  what else is installed, so identity is never the name: it is `(root, rel_path)` in the custom keys.
  A `.blend` per folder keeps names unique regardless.
- **C. Index both injection and removal, tagged, and let 7.4 give each its own shelf.** The INJ/REM
  pairs share names by design ("CDI Hitoro Head INJ"/"REM", 179 entries each), so one mixed shelf
  would show every pair twice. Two shelves (`asset_poll` on the tag) remove that without any naming
  trick, and put the placeholder-heavy removal tiles on a shelf you open on purpose.
- **D. Placeholder, and the `.rsr` decoder.** Placeholder: a flat tile coloured by kind, no text
  (Blender can't draw text onto an image in a background process, and the shelf prints the name under
  the tile anyway). Decoder: recommend **7.3 ships with placeholders and the decoder is a separate
  follow-up (7.3d)**, because 1,455 shown entries (37%) are affected, but a PICT v2 decoder is its
  own piece of work with its own test corpus. If you would rather have thumbnails first, it can go
  before 7.4.
- **E. Incomplete entries:** a tag and a `[INCOMPLETE: N of M files missing]` description prefix, no
  recoloured thumbnail (1 entry today; compositing images for it is not worth it). Entries with
  *every* target missing stay hidden (27).
- **F. Index location:** `user_resource('CONFIG', ...)` like the existing cache, rather than
  `bpy.utils.extension_path_user()`. Both are per Blender version; this keeps one place for the
  add-on's derived data. Say if you prefer the extension-specific path.

#### Risks and what is not verified

- **Cold-cache classification time: measured, 64.5 s serial, 40 s with parallel prefetch** (warm:
  9 to 11 s). Evicting every package file's cached pages with `posix_fadvise(DONTNEED)` and timing a
  full build on the owner's external drive; directory metadata stayed cached, so this is cold file
  *contents*, not a fully cold drive. Reading the heads on 32 threads first (`prefetch_heads`) cut it
  by 40%: the work is ~17,000 small reads, not CPU. A rebuild from a saved manifest takes 0.7 s.
  The first build still needs visible progress (the "reading" phase is almost all of it).
- **Custom keys read live through a shelf.** Readable through `bpy.data.libraries.load()` (prototype),
  but through the shelf's `AssetRepresentation` only the demo script ever showed them. If it fails,
  the payload falls back to the description string. Settled in 7.4, but the format is chosen here, so
  the description holds the path too. *Settled (7.4): read live through `context.asset.metadata` in a
  5.3 Asset Browser.*
- **Asset Browser with ~4,000 assets in 139 files.** The prototype read 2,346 entries in 88 files and
  10,000 stand-ins in one file without trouble; this exact size was not opened in the GUI.
- **A package whose leaf is read from a stale cache** (leaf changed but its mtime did not): accepted,
  rebuild fixes it.
- **Not doing, and why:** recoloured or framed thumbnails for incomplete entries (2 entries); a
  background auto-rebuild when roots change (the index is rebuilt on demand, and Preferences says
  when it is stale, since a surprise 10 to 60 s build on startup is worse than a stale tile);
  indexing figures (7.5); indexing poses and expressions (decision 1).

### 7.4 as built: using a package from the Asset Browser and the shelf (2026-11-17)

The 7.3 index could be browsed but not used. The owner asked for the Asset Browser itself to
apply packages, not only a shelf, so 7.4 covers both with one operator.

**Probed first, live (Blender 5.3 window, throwaway `BLENDER_USER_CONFIG`, the owner's real
3,814-asset library):** `context.asset.metadata.get("poser_rel_path")` (and `poser_root`,
`poser_kind`) returns the values written by the child for an *external* asset; tags, description
and `id_type == 'SCENE'` read back; `context.selected_assets` and `context.asset` work in the
Asset Browser; the view layer's active object is reachable from there. Blender's own asset index
cache (`~/.cache/blender/asset-library-indices/`) stores the keys under `user_properties`. This
settles the 7.3 risk "custom keys read live": the description fallback is kept only as a safety
net. Blender's own brush shelf filters on `asset.metadata.get(...)` the same way.

**What exists for acting on an asset (5.0.1 UI scripts and RNA, read, not assumed):**
`ASSETBROWSER_MT_context_menu` can be appended to; Asset Browser side-panel panels use
`bpy_extras.asset_utils.AssetBrowserPanel` in the `TOOL_PROPS` region; `AssetShelf` has
`bl_activate_operator`, `bl_drag_operator` and a `draw_context_menu` classmethod (the pose library
uses all three). **There is no Python hook for an asset dropped from the Asset Browser** (the only
drop handlers are `FileHandler`s, for files from the OS), and none for a double click. So the
Asset Browser gets a right-click entry and a side-panel button, not drag-and-drop.

**As built:**

- `poser.use_morph_package` (`operators/useMorphPackage.py`, `INTERNAL`, no `UNDO` of its own:
  it only starts Apply, the picker or Remove, which push their own undo steps). Reads the asset
  through `context_asset()` (one function, so a headless check can supply one; `temp_override(asset=…)`
  takes no Python stand-in), resolves it with `core/library_build.resolve_package_asset()` (the
  recorded root first, then every configured root, so a moved mount still works; plain Python,
  pytest-covered), and acts on the active mesh:
  - **injection**, mesh already has `poser_reference_obj` → straight to the 7.2 morph picker;
  - **injection**, first package on this mesh → Apply's popup with the package already picked in
    its list (Apply's `invoke()` now pre-selects a passed `injection_filepath`), so only the figure
    is left to choose. Either way there is still an OK before the mesh changes (decision 2);
  - **removal** → Remove runs straight away (`call_nested(..., execution='EXEC_DEFAULT')`). Remove
    only deletes the package's own shape keys, its report lists them, and it is one Ctrl+Z. This
    departs from decision 2's "one extra click" for the removal side only; proposed to the owner,
    not yet confirmed. A confirm popup is a few lines if wanted.
- `call_nested()` takes `execution`; Remove now reports through `say()` like Apply and the picker,
  so its messages show when the shelf starts it.
- `ui/packageAssets.py`: the right-click entry (**Apply to Active Mesh...** / **Remove from
  Active Mesh**, drawn only for our assets), a **Poser Morph Package** side panel in the Asset
  Browser (kind, which mesh it will act on, the button), and two 3D Viewport shelves,
  `VIEW3D_AST_poser_injection_packages` and `VIEW3D_AST_poser_removal_packages` (`filter_scene`,
  `asset_poll` on the kind, `NO_ASSET_DRAG` since a Scene stand-in has nowhere to drop, Object Mode
  with a mesh active). Not `DEFAULT_VISIBLE`, same as the pose library's shelf: the shelf region is
  opened from the viewport's **View → Asset Shelf**, rather than taking space under every mesh.

**Verified:** registration and unregistration on 5.0.1, 5.2.4 and 5.3.0; resolver tests (own keys,
tag/description fallback incl. the `[INCOMPLETE: …]` prefix, a moved root, a missing package,
someone else's asset); headless on 5.0.1 with a real Aiko3 import and a stand-in asset: poll
accepts ours and refuses others, removal through the operator removed `BrowHeavy` (1/1, report
written), injection reached Apply with the package path, the picker branch was taken when a figure
was remembered, and a missing package gives a message naming where it looked.

**Owner GUI test (2026-11-17):** the Asset Browser right-click entry, side panel and both shelves
work. The owner notes the right-click entry isn't where a user would look first, but it is enough
for now.

**Category-folder rule (owner, same test).** The library listed `.pz2` files from vendor folders
directly under `Runtime/libraries` (`!Adz/HiroGen/FrenulumAsymmetric.InjDeltas.pz2`). Those are
the files a package under `pose` `readScript`s, not packages to apply on their own, and Poser's
Library palette never shows them: it lists only its category folders. 7.3a only hid leaf files
by name (`InjDeltas.*`/`RemDeltas.*` prefix, or a `Deltas` folder), which caught `!DAZ` but not
`!Adz`'s `<morph>.InjDeltas.pz2` naming. Now an entry must also be inside one of Poser's
category folders (`library_index.POSER_LIBRARY_CATEGORIES`: camera, character, face, hair, hand,
light, materials, pose, props, scene; matched ignoring case). `MANIFEST_VERSION` 3, so an existing
index shows as needing a rebuild. Measured on the owner's 13 current roots, rebuilt in memory
(9.9 s): 3,814 → 3,783 shown, 132 → 129 folders; the 31 dropped are exactly the out-of-category
ones (`!Adz` 28, `Posermatic-INJ` 2, `Morphs` 1), all 31 are read by a package under `pose` (so
none becomes unreachable), nothing was added and no kept entry was renamed. The Apply/Remove
dropdowns (`runtime_index`) still list vendor-folder files: they are the manual fallback, and picking a
single leaf there is deliberate.

**Stale shelf after a rebuild (owner, same day).** After the category-folder Update, the Asset
Browser no longer listed the `!Adz` packages but the shelf still did. On disk the update was right
(version 3, 3,783 entries, the `!Adz` file gone). Probed on 5.3 against a scratch copy of the library,
shelf search narrowed to one folder's 3 assets, then that folder's `.blend` deleted: with no refresh
the shelf keeps all 3 tiles indefinitely; `asset.library_refresh` run in an Asset Browser *or* in the
shelf's own `ASSET_SHELF` region clears them. So there is no separate on-disk cache: Blender keeps
the loaded list in memory and doesn't watch the folder. (The shelf lists the "All Libraries"
reference, not ours by name.) The owner's browser was simply opened or switched after the update.
Fix: `libraryIndex._refresh_open_asset_lists()`, called from Build Library Index's `_finish`, runs
`asset.library_refresh` in every open Asset Browser and every 3D Viewport shelf region whose poll
passes (a shelf that never loaded a list reads fresh when first shown). Verified the same way: it
refreshed 2 views and the stale tiles went. A failure is reported as a warning naming Blender's own
**Refresh Asset Library**; the index itself is unaffected. Only probed on 5.3.

**GUI items above** (right-click entry, side panel, shelf clicks, dialogs from the Asset Browser):
confirmed by the owner's test. Still not exercised: Windows/macOS.

### Decisions

**Resolved (owner, 2026-11-14):**

1. **Which entries appear.** Poses and expressions are hidden. Poser pose assets have already
   proven painful to translate into Blender's coordinate system, and expressions fall in the same
   bucket for now. `.pmd` packages are out of scope and hidden (Phase 4 stays paused).
2. **A click** opens the Apply popup pre-filled with the package (Apply mutates the mesh, so one
   extra click is the safer default). Applying directly can come later.
3. **Entries with no thumbnail** get a placeholder (see the `.rsr` finding above).
4. **One index `.blend` per source folder.**
5. **Incomplete packages are shown and flagged**, not hidden. They remain loadable; Apply already
   skips unresolvable `readScript` targets and reports them. Packages with *every* target missing
   load nothing and are hidden. Flag = a dimmed thumbnail with a red frame, an `incomplete` tag, and
   "[INCOMPLETE: N of M files missing]" in the description. Judged against the *configured* roots
   (see the correction above): with all 20 of the owner's roots that is 3 flagged entries (1 missing
   file each) and 3 hidden, not the 11 and 62 the own-runtime-only run suggested; the demo library
   still reflects the own-runtime-only numbers.
6. **Minimum Blender version is 5.0.1.** Supported by the API history: `AssetShelf.filter_*` and
   `bl_drag_operator` were added in **5.0**, so the shelf as prototyped cannot exist on 4.x. Nothing
   in 5.1 to 5.3 is needed (5.1 added `ShapeKey.select`/`Object.shape_keys_selected`, which could
   later enable "remove the selected morphs"; 5.2/5.3 changed remote/project asset libraries,
   irrelevant here). `blender_version_min` (manifest, `bl_info`, README, `CLAUDE.md`) is now 5.0.1, which
   drops 4.4/4.5 users (they stay on 1.0.1). With a 5.0.1 floor the `hasattr(AssetShelf)` gate
   is unnecessary.
7. **Compressed (`.p2z`/gzip) entries are deferred.**

**Resolved (owner, 2026-11-15), for 7.2:**

8. **Default selection** is every "new" morph checked, with the estimate in the header; the list is
   mainly for unchecking. (Alternative was none checked.)
9. **Single-morph packages skip the list** and apply directly; the list is an extra click there.
   Built first as "exactly one *choosable* morph skips the list", which applied `pRubenesque` from
   the 95-morph `!All Body.pz2` with no dialog (owner UAT, 2026-11-15). Corrected to the decision's
   own wording: judged on the package, so a long package with one new morph still shows its list.
10. **Off-thread package parsing is a follow-up**, not folded into 7.2.

**Deferred idea.** Expressions act on morph channels only, so they could in principle be applied as
shape-key *values* on a figure, unlike poses. A separate feature (it touches `targetGeom` *value*
channels, not deltas), not part of Phase 7.

**Open: same names across folders.** One file per folder removes the datablock collision (names
are unique within a folder), but names still repeat *across* files: 409 names, e.g. `Emaciated` ×8
and `BrowHeavy` ×5. The repeats are different products, not noise: the five `BrowHeavy` are A3/H3,
David Head INJ, David Head **REM**, V3 Features and Michael 4. The folder is what tells them apart.
Candidate handling, cheapest first: (a) plain names; the catalog tree (folder) and the tooltip
(description = path) carry the product; (b) additionally suffix only the colliding names with the
parent folder, `BrowHeavy (!V3 Features INJ)`; (c) use the package's resolved morph names (7.6) as
the label where the entry name is generic. Not decided: waiting on the owner to look at the demo
(plain names, per-folder files) in the live shelf. Untested: how the Asset Browser/shelf list
same-named assets from different files.

### Not doing, and why

- Custom ID/asset types: impossible from Python (above).
- `.pmd` packages: Phase 4 is paused; they are hidden from the library, not applied.
- Writing the index into the user's Poser content: it's their library; the index is derived data.
- Embedding the thumbnail grid in our own popup: `template_asset_view` was removed in 5.0.

### Still unverified

~~Live reading of custom `asset_data` keys through `asset.metadata`~~ (read live in a 5.3 Asset
Browser, 7.4; the shelf hands the same `AssetRepresentation`, not yet clicked live); shelf
behavior for same-named assets across files; the Asset Browser with 10k+ Image assets in a real
session; applying the 259-morph Victoria 3 head package (no V3 figure here, so per-morph cost at
that size is extrapolated); the asset API's stability (5.3 added projects/remote-library
parameters; additive so far). Prototype scripts live in the session scratchpad and are not
committed.

## Open decisions

- ~~Phase 1: `mathutils.kdtree` vs. pure-numpy.~~ Resolved: pure numpy, stays pytest-testable.
- ~~Where Phases 1–2's new modules live.~~ Resolved: all in `core/cr2/` (`mesh_correspondence.py`,
  `actor_vertex_index.py`, `poser_paths.py`, `injection_package.py`, `apply_injection.py`) — same
  cohesion reasoning, held up through Phase 3.
- ~~Whether/how Phase 3's new shape keys interact with the existing consolidation flow.~~ Resolved
  in Phase 5: JCM-named morphs are skipped (reuses `is_jcm_shapekey()`, same reasoning as
  consolidation), and there's no merge/split step needed — an injected morph is already one combined
  shape key per name, unlike raw FBX-baked ones. No driver/ERC wiring attempted.
- Phase 4 (PMD) — paused indefinitely per owner (2026-09-12), not just "needs a spike." No plan to
  resume without a specific reason to.
- New, from Phase 5's real end-to-end run: Phase 1/3's documented 97.7%/68.1% match rate and
  BrowHeavy's `collisions_resolved=500` were measured *without* `remove_loose_verts()` — the real
  add-on operator's own post-import cleanup, which the Phase 1/3 scratch scripts bypassed. The same
  BrowHeavy morph through the *real* import+apply pipeline got zero collisions. Not re-verified at
  full-figure scale — worth revisiting if a future session has reason to touch Phase 1's match-rate
  numbers again.
- ~~Correspondence caching across multiple `poser.apply_morph_injection` runs on the same mesh.~~
  Done in Phase 7.1: the owner's one-at-a-time use made the repeated ~34s build the real complaint.
- Importing an injection package onto a *separate* geometry object (to apply as a shape key later,
  rather than directly onto the currently-active mesh) — raised during Phase 5.2's UAT, owner's own
  call to defer: ties into the planned `mesh_tools` merge, which will bring over the mirror-image
  feature (exporting an existing shape key as its own geometry object). Not scoped until that
  merge happens.

## Test data

- Reference OBJs: `blaiko3.obj` (Aiko3), `LaFemme1.obj` (LaFemme) — both now in
  `Test_Poser_Assets/`, sourced from the owner's DAZ Connect library / Poser content library.
- Real injection packages (owner's Poser content library, external mount, not copied into
  `Test_Poser_Assets/` — paths are machine-specific):
  - `.../Base Figures/Runtime/libraries/pose/!A3-H3 MORExp1 INJ/` (Aiko3/Hiro3, inline deltas)
  - `.../Base Figures/Runtime/libraries/pose/!SP All Morphs INJ/` (Stephanie 3, inline deltas)
  - `.../Base Figures/Runtime/libraries/pose/La Femme/Body Morphs/` (LaFemme, PMD-referenced)
- Verification scripts (session-local scratch, not committed): vertex-correspondence comparison
  (Procrustes-fit index test + coarse-align/nearest-neighbor test) run against Aiko3 and LaFemme.
