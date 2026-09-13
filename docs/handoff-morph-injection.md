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
- Correspondence caching across multiple `poser.apply_morph_injection` runs on the same mesh —
  still deferred post-Phase 5.2; the real-run cost (now ~32s, down from ~5 minutes) is dominated by
  one `build_vertex_correspondence()` call regardless of package size, so this remains the lever if
  a *second* run's cost specifically becomes a real complaint — Phase 5.2 only fixed the first run.
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
