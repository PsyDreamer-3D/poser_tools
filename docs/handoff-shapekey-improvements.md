# Handoff: Shape-Key Handling Improvements

Written for a fresh session with no prior context. Read `../CLAUDE.md` first for layout and design notes, then this document for the actual work.

## Why this exists

`poser_tools`' FBX importer consolidates Poser's split parent/child morphs (a full-body morph plus per-body-part deltas that FBX export leaves as separate shape keys) into single usable shapekeys — see `poser_tools/core/functionsShapeKeys.py`, driven by `poser_tools/operators/fixPoserShapekeys.py`.

A sibling private repo, `cr2_importer` (a.k.a. "Poser Bridge", `PsyDreamer-3D/cr2_importer`), solves a related problem from a stronger position: it parses Poser's `.cr2` files directly, so it has access to the actual authored channel relationships (ERC / `valueOpDeltaAdd` links) that FBX export discards. Comparing the two surfaced five concrete improvements for `poser_tools`. Original priority was
JCM-first, but the work order was changed to put diagnostics first — Phases 1, 3 and 4 all report
through the same channel, and diagnostics is the only phase not blocked on real Poser test data:

1. **Diagnostics** via `self.report()` + a `bpy.data.texts` block, replacing `print()` — **done** (branch `feature/shapekey-diagnostics`)
2. JCM detection/exclusion
3. Overlap-safe delta accumulation
4. Round-trip merge metadata
5. (Longer-term, spike first) Optional CR2 cross-reference for ground-truth canonical naming

Each phase is independently shippable. Follow this repo's phased-work discipline: plan → confirm with the project owner → implement → manual test in Blender → move to the next phase. Don't bundle phases into one PR/commit unless asked. **Section headings below keep the original numbering** (Phase 1 = JCM, Phase 2 = diagnostics, …).

## Current code map

- `poser_tools/operators/fixPoserShapekeys.py` — `OT_FixPoserShapekeys_Operator`. Guards against re-running via `obj["morphs_consolidated"]` and against a mesh with no shape keys, calls `consolidate_poser_shapekeys()`, emits a `self.report({'INFO'}, …)` summary and writes the full log to the `"Poser Shapekey Report"` text block via `core.utils._write_report`.
- `poser_tools/core/functionsShapeKeys.py` — all the actual logic:
  - `_TRAILING_DIGITS_RE = re.compile(r'\.[0-9]{3}')`, `_PBM_RE = re.compile(r'^PBM')`
  - `is_child_shapekey(sh_name, _is_daz)` — trailing-digit or (if `_is_daz`) `p`/`PBM`-prefix detection
  - `get_parent_name(sh_name, _is_daz)` — strips the child marker; **can return `None` silently** if neither condition matches
  - `build_parent_shapekey_list()` / `build_child_shapekey_list()` / `build_fbm_shapekey_list()` — group children under parents; promotes orphaned numbered children with no parent present
  - `accumulate_fbm_shapekey()` — sums each child's delta (`child_co - basis_co`) onto the FBM, unconditionally, no overlap check
  - `consolidate_poser_shapekeys()` — orchestrates: mute all, read basis, pre-read all child coords, accumulate per FBM, delete merged children, unmute processed, rename promoted orphans. Returns a summary dict (`consolidated` / `working_kept` / `empty_skipped` / `promoted` / `renamed` / `children_deleted` / `log`); `build_fbm_shapekey_list()` and `build_parent_shapekey_list()` take an optional `log` list
- `poser_tools/properties/poserToolsAddonSettings.py` — `PoserShapeKeysAddon_Settings(bpy.types.PropertyGroup)`, currently has `is_daz`, `weight_group_prefix`, `bone_prefix`.
- `poser_tools/ui/fixPoserShapekeys.py` — `FixPoserShapekeys_Panel`, draws the `is_daz` checkbox and the operator button.
- `poser_tools/core/utils.py` — `_write_report(context, text_name, lines)` (the scaffold's text-block diagnostics helper) and the shared `_TAB` panel-category constant. Already present; Phase 2 wires it in.

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

**Goal:** JCM (joint-corrective) shape keys are excluded from consolidation entirely, mirroring `cr2_importer`.

**Decided:** always skip JCM keys — no UI toggle. `cr2_importer`'s `skip_jcm` param exists but every call site hardcodes `True`; matching that keeps the surface small. The report still lists what was excluded.

**Open sub-question — needs real data before writing the matcher:** the exact naming Poser's FBX exporter uses for JCM channels hasn't been verified in this project. `cr2_importer` matches its own CR2 channel names (`internal_name.startswith('JCM')`), a different string than whatever survives FBX export + Blender shape-key naming. **First step: inspect actual shape-key names from a real Poser FBX with known JCM morphs** before hardcoding a pattern. Owner has generated test files with morph data; there are also sample pairs in `../FBX_Weightmap_Tests/` (`Legacy-Michael4.fbx`, `Legacy-Vicky4.fbx`, `Legacy-Hiro3.fbx`, …) but not all contain morphs — verify first. The vendored `vendor/io_scene_fbx/parse_fbx.py` can dump blend-shape channel names without a full Blender import.

**Files to touch:**
- `core/functionsShapeKeys.py`: add `is_jcm_shapekey(sh_name) -> bool` near `is_child_shapekey()`. Call it in `build_parent_shapekey_list()` **and** `build_child_shapekey_list()` so a JCM key can't slip in as an unclaimed "parent". Add the excluded names to the summary dict (`"jcm_excluded"`) and the log.
- `operators/fixPoserShapekeys.py`: fold the JCM count into the `self.report` summary line.
- No `properties/` or `ui/` change (no toggle).

**Acceptance test:** run Fix Poser Shapekeys on a figure with known JCM morphs. JCM keys stay untouched (not merged, not deleted, not renamed) and appear in the `"Poser Shapekey Report"` text block as `excluded (JCM)`.

---

## Phase 2 — Diagnostics via `self.report()` + text block — **DONE**

Branch `feature/shapekey-diagnostics`. Implemented:
- `consolidate_poser_shapekeys()` returns a summary dict with a `log` list; every `print()` is gone
  from `core/functionsShapeKeys.py` and `operators/fixPoserShapekeys.py`. `build_fbm_shapekey_list()`
  / `build_parent_shapekey_list()` take an optional `log`.
- `OT_FixPoserShapekeys_Operator.execute()` emits a one-line `self.report({'INFO'}, …)` summary and
  writes the full itemized log to the `"Poser Shapekey Report"` text block via
  `core.utils._write_report()`.
- Added guards: `self.report({'ERROR'}, …)` when the active mesh has no shape keys (was an
  `AttributeError`); `self.report({'INFO'}, …)` on the already-consolidated no-op.
- No behavior change to the consolidation math or grouping. The summary-dict keys
  (`consolidated` / `working_kept` / `empty_skipped` / `promoted` / `renamed` / `children_deleted` /
  `log`) are the extension point for Phases 1, 3, 4 — add keys, don't change the signature.

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

- ~~Phase 2 (diagnostics): `core/` package now, or keep flat?~~ Done — `core/` exists, Phase 2 shipped on `feature/shapekey-diagnostics`.
- ~~Phase 1: user-facing JCM toggle or always-skip?~~ Decided — always skip, no toggle.
- Phase 5: vendor a trimmed CR2 parser into `poser_tools`, or extract a shared library both add-ons depend on?
- Phase 5: needs one real `.cr2` + matching `.fbx` pair to verify naming correspondence — who can provide these?
