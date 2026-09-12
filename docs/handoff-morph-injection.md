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

### Phase 3 — Apply an inline-delta injection to a mesh

**Goal:** given an already-imported mesh + its reference OBJ + a parsed inline-delta channel group
(from `core/cr2/cr2_parser.py`, potentially spanning several actors — `core/cr2/name_match.py`
already groups a morph name across actors), build one new Blender shape key with the combined,
correctly-placed deltas.

**Design:** reuse patterns already proven in this codebase — `core/functionsShapeKeys.py`'s
mute-management and slider setup, and the basis-copy + scatter-add accumulation pattern
`cr2_importer`'s `ShapeKeyImporter._build_one()` uses (`np.add.at` onto a basis copy). Orchestrator
`.pz2` resolution (locating the `Runtime` root, walking `readScript` references) is part of this
phase, not its own — it's plumbing, not a design risk.

**Acceptance test:** apply `! All Morphs.pz2` (Aiko3/Hiro3 pack) or `!SP All Morphs INJ` to a
freshly-imported figure; confirm the new shape keys appear, are named sensibly, and visibly
deform the mesh correctly when dialed up (owner verifies in Blender — I can't eyeball a morph's
correctness, only that indices/counts line up).

### Phase 4 — PMD binary reader (separate; spike first)

**Goal:** read a PMD's (`PZMD` magic) per-channel delta arrays, for `injectPMDFileMorphs`-style
packages (confirmed: La Femme's "Body Morphs Pro", likely most RPublishing/modern content).

**Explicitly deferred, not scoped yet.** Nobody — not `poser_tools`, not `cr2_importer` — has a
PMD reader today. This needs its own investigation (binary layout is undocumented as far as
anything in this codebase shows) before any design, same discipline as the CR2 parser bug and the
original naming spike got. Do not start on this without a dedicated spike.

### Phase 5 — User-facing operator

**Goal:** wire Phases 1–3 into an actual operator (a file picker for the injection `.pz2`, applied
to the active mesh), following existing conventions — `self.report()` + a text-block report
(matching `_write_report()` / the `"Poser Shapekey Report"` pattern), `obj["..."]` bookkeeping for
what was already applied so re-running is a no-op or an explicit re-apply.

**Deliberately last.** Each of Phases 1–3 is independently verifiable without any Blender UI; build
and confirm those before wiring up an operator around them, matching how the original shape-key
consolidation phases were built as pure logic first and only wired into `OT_ImportPoserFBX` in a
later, separate change.

## Open decisions

- Phase 1: `mathutils.kdtree` (needs `bpy`) vs. a pure-numpy spatial index (stays pytest-testable,
  consistent with the rest of `core/cr2/`). Leaning numpy; not yet decided.
- Where Phases 1–2's new modules live: proposed to stay inside `core/cr2/` (e.g.
  `mesh_correspondence.py`, `obj_groups.py`) for cohesion with the rest of the Poser-data toolkit,
  even though they're about OBJ/geometry correspondence rather than CR2/PZ2 text parsing per se.
  Not yet confirmed.
- Whether/how Phase 3's new shape keys interact with the existing consolidation flow
  (`core/functionsShapeKeys.py`) — e.g. does an injected morph get JCM-checked, merge-recorded,
  etc., the same way FBX-native ones do? Not addressed yet.
- Phase 4 (PMD) needs its own real spike before any design — explicitly not scoped here.

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
