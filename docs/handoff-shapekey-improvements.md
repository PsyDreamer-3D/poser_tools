# Handoff: Shape-Key Handling Improvements

Written for a fresh session with no prior context. Read `../CLAUDE.md` first for repo structure notes, then this document for the actual work.

## Why this exists

`poser_tools`' FBX importer consolidates Poser's split parent/child morphs (a full-body morph plus per-body-part deltas that FBX export leaves as separate shape keys) into single usable shapekeys — see `poser_tools/operators/functionsShapeKeys.py`, driven by `poser_tools/operators/fixPoserShapekeys.py`.

A sibling private repo, `cr2_importer` (a.k.a. "Poser Bridge", `PsyDreamer-3D/cr2_importer`), solves a related problem from a stronger position: it parses Poser's `.cr2` files directly, so it has access to the actual authored channel relationships (ERC / `valueOpDeltaAdd` links) that FBX export discards. Comparing the two surfaced five concrete improvements for `poser_tools`, agreed with the project owner in priority order:

1. JCM detection/exclusion
2. Diagnostics via `self.report()` + a `bpy.data.texts` block, replacing `print()`
3. Overlap-safe delta accumulation
4. Round-trip merge metadata
5. (Longer-term, spike first) Optional CR2 cross-reference for ground-truth canonical naming

Each phase is independently shippable. Follow this repo's phased-work discipline: plan → confirm with the project owner → implement → manual test in Blender → move to the next phase. Don't bundle phases into one PR/commit unless asked.

## Current code map

- `poser_tools/operators/fixPoserShapekeys.py` — `OT_FixPoserShapekeys_Operator`. Guards against re-running via `obj["morphs_consolidated"]`, calls `consolidate_poser_shapekeys()`, reports via bare `print()`.
- `poser_tools/operators/functionsShapeKeys.py` — all the actual logic:
  - `_TRAILING_DIGITS_RE = re.compile(r'\.[0-9]{3}')`, `_PBM_RE = re.compile(r'^PBM')`
  - `is_child_shapekey(sh_name, _is_daz)` — trailing-digit or (if `_is_daz`) `p`/`PBM`-prefix detection
  - `get_parent_name(sh_name, _is_daz)` — strips the child marker; **can return `None` silently** if neither condition matches
  - `build_parent_shapekey_list()` / `build_child_shapekey_list()` / `build_fbm_shapekey_list()` — group children under parents; promotes orphaned numbered children with no parent present
  - `accumulate_fbm_shapekey()` — sums each child's delta (`child_co - basis_co`) onto the FBM, unconditionally, no overlap check
  - `consolidate_poser_shapekeys()` — orchestrates: mute all, read basis, pre-read all child coords, accumulate per FBM, delete merged children, unmute processed, rename promoted orphans
- `poser_tools/settings/poserToolsAddonSettings.py` — `PoserShapeKeysAddon_Settings(bpy.types.PropertyGroup)`, currently has `is_daz`, `weight_group_prefix`, `bone_prefix`.
- `poser_tools/panels/fixPoserShapekeys.py` — `FixPoserShapekeys_Panel`, draws the `is_daz` checkbox and the operator button.

## Reference material from `cr2_importer` (private repo — patterns only, not code to copy verbatim; the data model is different: CR2 channels vs. baked FBX shape keys)

**JCM skip** (`core/shape_key_importer.py`):
```python
if self.skip_jcm and ch.internal_name.startswith('JCM'):
    continue
```
Documented in that repo's `AGENTS.md`: *"Joint-corrective morphs (JCM\*) are skipped by default"* — treated as a distinct category from FBM/PBM dial morphs, not something to merge or expose as a slider. Default `skip_jcm=True`, user-toggleable.

**Round-trip metadata** (same file):
```python
bl_obj.data['poser_internal_names'] = _json.dumps(_internal_names)
non_zero = {k: v for k, v in _morph_values.items() if v != 0.0}
if non_zero:
    bl_obj.data['poser_morph_defaults'] = _json.dumps(non_zero)
```
Stored on the *mesh* data-block (not the shape key — `bpy.types.ShapeKey` doesn't support ID properties). A downstream operator (`APPLY_OT_poser_pz2_pose`) reads this back, and degrades gracefully with a `self.report({'WARNING'}, ...)` when the property is missing rather than failing.

**Diagnostics convention**, from that repo's `AGENTS.md` (hard constraint, not a style choice): *"On Linux, Blender's system console is not accessible from the UI. Any output a person needs to read should use `self.report(...)` (inside operators) or a `bpy.data.texts` block for longer reports. Do not rely on `print()` alone."*

**`_write_report()` helper** (from the `blender-addon-scaffold` template — use this pattern, not a bespoke one):
```python
def _write_report(context, text_name: str, lines: list[str]) -> None:
    """Write *lines* to a bpy.data.texts block and focus any open Text Editor on it."""
    if text_name in bpy.data.texts:
        tb = bpy.data.texts[text_name]
        tb.clear()
    else:
        tb = bpy.data.texts.new(text_name)
    tb.write("\n".join(lines))
    for area in context.screen.areas:
        if area.type == 'TEXT_EDITOR':
            area.spaces.active.text = tb
            break
```

---

## Phase 1 — JCM detection/exclusion

**Goal:** JCM (joint-corrective) shape keys are excluded from consolidation entirely, mirroring `cr2_importer`'s default.

**Open sub-question to resolve before writing the regex:** the exact naming convention Poser's FBX exporter uses for JCM channels hasn't been verified against real exported data in this project — `cr2_importer` only had to match its own internal CR2 channel names (`internal_name.startswith('JCM')`), which is a different string than whatever survives FBX export/Blender's shape-key naming. **First step: get one real Poser FBX export containing known JCM morphs and inspect the actual shape-key names** before hardcoding a pattern. Don't assume `startswith('JCM')` transfers unchanged.

**Files to touch:**
- `functionsShapeKeys.py`: add `is_jcm_shapekey(sh_name) -> bool` near `is_child_shapekey()`. Wire it into `build_parent_shapekey_list()` and `build_child_shapekey_list()` so JCM matches are skipped from both, not just one (a JCM key could otherwise slip in as an unclaimed "parent").
- `settings/poserToolsAddonSettings.py`: add `exclude_jcm: BoolProperty(name="Exclude JCM Morphs", description="Skip joint-corrective (JCM) morphs during consolidation", default=True)`.
- `panels/fixPoserShapekeys.py`: add `row.prop(options, "exclude_jcm")` alongside the existing `is_daz` row.
- `operators/fixPoserShapekeys.py`: pass `options.exclude_jcm` through to `consolidate_poser_shapekeys()`.

**Acceptance test:** import an FBX with known JCM morphs, run Fix Poser Shapekeys with the new option on (default). JCM keys should remain untouched (not merged, not deleted, not renamed) and should be visible in whatever Phase 2 reporting produces as "excluded — JCM."

---

## Phase 2 — Diagnostics via `self.report()` + text block

**Goal:** Replace every `print()` in `fixPoserShapekeys.py` and `functionsShapeKeys.py` with the scaffold's reporting convention.

**Open decision — resolve with the project owner before starting:** this repo has no `core/` package (see `CLAUDE.md` structure notes). Two options:
- (a) Add `_write_report()` as a new function directly in `functionsShapeKeys.py` — smaller diff, keeps the existing flat structure.
- (b) Introduce `poser_tools/core/utils.py` now, matching scaffold convention, and move shared report logic there for future operators to reuse too.

Default to (a) unless told otherwise — this phase shouldn't turn into an unplanned repo restructure.

**Behavior split:**
- Short outcome summary → `self.report({'INFO'}, ...)` on `OT_FixPoserShapekeys_Operator` (e.g. `"Consolidated 12 morph(s), excluded 4 JCM, promoted 1 orphan"`).
- Full itemized log (every merge, every JCM exclusion, every orphan promotion, every empty-shapekey skip — everything currently a `print()` call) → a named `bpy.data.texts` block (e.g. `"Poser Shapekey Report"`), auto-focused in any open Text Editor, via `_write_report()`.
- `consolidate_poser_shapekeys()` currently returns nothing; it'll need to either accept a list to append log lines into, or return a `(fbm_shapekeys, log_lines)` tuple, so the operator can pass `log_lines` to `_write_report()` after the function completes.

**Acceptance test:** run Fix Poser Shapekeys with no open Text Editor area — confirm no `print()` output is the only trace (check by running Blender from a terminal and confirming console output is now just the`self.report()` line, not the itemized log). Then split the viewport to add a Text Editor and re-run — confirm it auto-focuses the report.

---

## Phase 3 — Overlap-safe delta accumulation

**Goal:** `accumulate_fbm_shapekey()` currently sums every child's delta onto the FBM unconditionally:
```python
for child_co in child_coords.values():
    result_co += child_co - basis_co
```
If two children both move the same vertex (a real risk — Poser's per-actor morph split isn't guaranteed disjoint), this silently double-counts. Default behavior should **stay additive** — that's correct for the normal non-overlapping case — this phase only adds *detection and reporting* of the failure mode, not a behavior change to the happy path.

**Approach:** before summing, compute a per-vertex "touched by more than one child" mask (nonzero delta on more than one child, above some epsilon to ignore float noise). Any overlapping vertex range gets logged (via the Phase 2 reporting path) with the FBM name, the conflicting child names, and the affected vertex count — not silently absorbed.

**Acceptance test:** construct or find a figure with known overlapping child morphs (if none is known to exist, this may need a synthetic test mesh with two shape keys deliberately sharing a moved vertex) and confirm the overlap is reported, not swallowed.

---

## Phase 4 — Round-trip merge metadata

**Goal:** after consolidation, record what happened so a later session (or a future "apply pose" style operator, if `poser_tools` ever grows one) can inspect it — mirroring `cr2_importer`'s `poser_internal_names` pattern, adapted to what FBX actually gives us (no internal channel names survive, so this is structural, not a name-resolution map).

**Shape:** write to `obj.data['poser_shapekey_merges']` as JSON:
```json
{
  "version": 1,
  "merges": {
    "BreastSize": {"children": ["BreastSize.001", "BreastSize.002"], "promoted_orphan": false}
  }
}
```
Write this from `consolidate_poser_shapekeys()` right before the function returns, using data already computed in `fbm_shapekeys`. This replaces the sole existing signal (`obj["morphs_consolidated"] = True`) with something inspectable, while keeping that boolean too since the operator's `poll()`/early-return still needs a fast re-run guard.

**Acceptance test:** run Fix Poser Shapekeys, then inspect `bpy.data.objects[name].data['poser_shapekey_merges']` in the Python console (or via a small throwaway script) and confirm it accurately reflects what was merged.

---

## Phase 5 — CR2 cross-reference (spike first — do not commit to a design yet)

**Goal:** optionally use the source `.cr2`/`.crz` file (if the user has it) to get a ground-truth `internal_name → canonical_name` map instead of guessing from FBX shape-key name patterns, closing the gap described in `CLAUDE.md`'s "Key design decisions."

**Two unknowns to resolve before writing any consolidation logic — this is a spike, not a build:**

1. **Naming correspondence is unverified.** We don't know whether Poser's FBX exporter names blend shapes after the CR2 channel's `internal_name`, `display_name`, or something FBX-sanitized/truncated. Get one real `.cr2` and its matching exported `.fbx` for the same figure, parse the CR2's channel names, and diff them against the actual shape-key names Blender ends up with after importing the FBX. Do this before writing a matcher — if the names don't correspond cleanly, the whole approach needs rethinking, not patching.
2. **Vendoring decision.** Getting `cr2_importer`'s four-tier canonical map (`ShapeKeyImporter._build_canonical_name_map()` — BODY valueParm ERC link, then cross-actor targetGeom ERC link, then `p`-prefix, then raw name) means parsing CR2 channel/ERC data, which means bringing a trimmed `cr2_parser.py`/`poser_io.py` into `poser_tools`. `poser_tools` already vendors `io_scene_fbx` as its own copy (see `poser_tools/vendor/`), so the precedent is to vendor rather than share code between the two add-ons — but confirm with the project owner before duplicating parser code, in case a shared library is preferred instead.

Do not start implementation on this phase without both unknowns resolved and a plan approved — per this repo's phased-work policy, this one clearly crosses the "architectural change touching core logic" threshold.

---

## Summary of open decisions for the project owner

- Phase 2: `core/` package now, or keep `functionsShapeKeys.py` flat?
- Phase 5: vendor a trimmed CR2 parser into `poser_tools`, or extract a shared library both add-ons depend on?
- Phase 5: needs one real `.cr2` + matching `.fbx` pair to verify naming correspondence — who can provide these?
