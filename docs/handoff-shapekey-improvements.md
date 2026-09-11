# Handoff: Shape-Key Handling Improvements

Written for a fresh session with no prior context. Read `../CLAUDE.md` first for layout and design notes, then this document for the actual work.

## Why this exists

`poser_tools`' FBX importer consolidates Poser's split parent/child morphs (a full-body morph plus per-body-part deltas that FBX export leaves as separate shape keys) into single usable shapekeys — see `poser_tools/core/functionsShapeKeys.py`, driven by `poser_tools/operators/fixPoserShapekeys.py`.

A sibling private repo, `cr2_importer` (a.k.a. "Poser Bridge", `PsyDreamer-3D/cr2_importer`), solves a related problem from a stronger position: it parses Poser's `.cr2` files directly, so it has access to the actual authored channel relationships (ERC / `valueOpDeltaAdd` links) that FBX export discards. Comparing the two surfaced five concrete improvements for `poser_tools`. Original priority was
JCM-first, but the work order was changed to put diagnostics first — Phases 1, 3 and 4 all report
through the same channel, and diagnostics is the only phase not blocked on real Poser test data:

1. **Diagnostics** via `self.report()` + a `bpy.data.texts` block, replacing `print()` — **done** (`feature/shapekey-diagnostics`)
2. **JCM detection/exclusion** — **done** (`feature/jcm-exclusion`) — JCM keys are *deleted*
3. **Overlap-safe delta accumulation** — **done** (`feature/overlap-detection`) — detection-only tripwire; real Poser splits are disjoint
4. **Round-trip merge metadata** — **done** (`feature/merge-metadata`) — `obj.data['poser_shapekey_merges']` JSON
5. (Longer-term, spike first) Optional CR2 cross-reference for ground-truth canonical naming ← next

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

## Phase 1 — JCM detection/exclusion — **DONE**

Branch `feature/jcm-exclusion`.

**Naming — investigated, resolved.** Parsed the `Shape` geometry names out of three real binary-FBX
exports (`~/Desktop/LaFemme.fbx`, `Aiko3.fbx`, `Kira.fbx`) the same way
`vendor/io_scene_fbx/import_fbx.py:blen_read_shapes()` derives shape-key names, then applied
Blender's `.NNN` dedup. `name.startswith('JCM')` is the right matcher:

| | LaFemme | Aiko3 | Kira |
|---|--:|--:|--:|
| shape keys | 300 | 638 | 169 |
| `startswith('JCM')` | 156 | 18 | 6 |
| spaced `"JCM Left Knee Bend 90"` | 33 | 0 | 0 |
| camelCase `"JCMrElbowBend130"` | 123 | 18 | 6 |
| JCM keys with a `.NNN` dedup suffix | 89 | 8 | 2 |

- Both naming forms, and the `.NNN`-suffixed duplicates, are caught by `startswith`.
- Must be `startswith`, **not** `'JCM' in name` — LaFemme ships a real control morph
  `"ON <- Use JCM -> OFF"` that a substring test would wrongly exclude.

**JCM keys are deleted, not preserved.** First cut left them in place ("exclude from
consolidation"); the owner reviewed real output and wanted them gone. FBX export bakes the JCM
*shape* but discards the ERC/driver relationship that fires it, so a JCM key on the imported mesh
can never function — it's dead weight (156 of LaFemme's 196 keys). `cr2_importer`, working from the
`.cr2`, never creates them; deleting matches that end state.

**Implemented:**
- `core/functionsShapeKeys.py`: `is_jcm_shapekey(sh_name)` (= `sh_name.startswith('JCM')`).
- `consolidate_poser_shapekeys()`: deletes every JCM key up front (`obj.shape_key_remove`), before
  `build_fbm_shapekey_list()` / `mute_all_shapekeys()` — so no interaction with the rest of the
  flow. Records names in `"jcm_removed"` and the log.
- `operators/fixPoserShapekeys.py`: `", removed N JCM"` appended to the `self.report` summary.
- Always delete — no UI toggle.

**Verified end-to-end in headless Blender** (fresh `import_scene.fbx` → `poser.fix_poser_shapekeys`):
- LaFemme: 301 → 40 keys, all 156 JCM removed, 0 `.NNN` leftovers, `"ON <- Use JCM -> OFF"` kept.
- Aiko3 (`is_daz=True`): 639 → 202 keys, all 18 JCM removed, 0 `.NNN` leftovers — consolidation
  counts (84 / 116 / 1) match the owner's earlier manual run.

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
  `jcm_removed` / `overlaps` / `merge_record` / `log`) — add keys, don't change the signature.

---

## Phase 3 — Overlap-safe delta accumulation — **DONE**

Branch `feature/overlap-detection`.

**The premise doesn't fire in practice.** `accumulate_fbm_shapekey()` sums every child's delta onto
the FBM. If two children of the same FBM both moved a vertex, the sum would double-count. Probed all
three figures in Blender (LaFemme / Aiko3 / Kira, 69 multi-child FBM groups): **zero overlapping
vertices, even at epsilon 1e-7.** Poser splits a full-body morph into per-actor children with
exactly-disjoint vertex sets and FBX export keeps that partition.

**So this ships as a tripwire, not a fix:**
- `_detect_child_overlap(child_coords, basis_co)` in `core/functionsShapeKeys.py` — per-child
  displacement mask (`> _OVERLAP_EPS = 1e-5` world units), returns `{vert_count, pairs}` or `None`.
- `accumulate_fbm_shapekey()` returns that; the math is **unchanged** (still an unconditional sum).
- `consolidate_poser_shapekeys()` collects hits into `result["overlaps"]` (`{fbm: {vert_count,
  pairs}}`) and writes `WARNING:` lines into the report log naming the FBM, the shared vert count,
  and the conflicting child pairs.
- `OT_FixPoserShapekeys_Operator` escalates its `self.report` to `{'WARNING'}` and adds a banner
  line to the report header when `overlaps` is non-empty.

**Verified in headless Blender:** LaFemme / Aiko3 run clean (no warning, consolidation counts
unchanged). Injecting a synthetic overlap into two of `MouthOpen`'s children → operator emits the
`{'WARNING'}` and the report lists `MouthOpen.001 & MouthOpen.002: 1 shared vert(s)`.

---

## Phase 4 — Round-trip merge metadata — **DONE**

Branch `feature/merge-metadata`.

`consolidate_poser_shapekeys()` serialises a record to `obj.data['poser_shapekey_merges']` (JSON
string — `bpy.types.Mesh` takes ID properties, `bpy.types.ShapeKey` does not) right before it
returns, and also returns it as `result["merge_record"]`:
```json
{
  "version": 1,
  "merges": {
    "<final shape-key name>": {"children": ["BreastSize.001", "BreastSize.002"], "promoted_orphan": false}
  },
  "renamed": {"pPregnant": "Pregnant"}
}
```
- Keyed by the **final** shape-key name (post promoted-orphan rename). `renamed` maps original → final.
- `merges` holds only the FBMs that actually absorbed children (`result["consolidated"]`); childless
  `working_kept` / `empty_skipped` morphs aren't listed. JCM removals and overlap hits stay in the
  report text block, not this record — bump `version` if a consumer needs them.
- `obj["morphs_consolidated"] = True` stays as the fast re-run guard.

Lets a later pass (e.g. resolving a PZ2 pose that dials `BreastSize.002`) map a pre-merge child
name back to the surviving morph.

**Verified in headless Blender:** LaFemme (21 merges / 105 children), Aiko3 `is_daz` (84 / 419),
Kira (0 merges, empty record still written). Every merge key is a surviving shape key; every listed
child is gone; every rename source absent / target present.

---

## Phase 5 — CR2 cross-reference (spike first — do not commit to a design yet)

**Goal:** optionally use the source `.cr2`/`.crz` file (if the user has it) to get a ground-truth `internal_name → canonical_name` map instead of guessing from FBX shape-key name patterns, closing the gap described in `CLAUDE.md`'s "Key design decisions."

**Two unknowns to resolve before writing any consolidation logic — this is a spike, not a build:**

1. **Naming correspondence is unverified.** We don't know whether Poser's FBX exporter names blend shapes after the CR2 channel's `internal_name`, `display_name`, or something FBX-sanitized/truncated. Get one real `.cr2` and its matching exported `.fbx` for the same figure, parse the CR2's channel names, and diff them against the actual shape-key names Blender ends up with after importing the FBX. Do this before writing a matcher — if the names don't correspond cleanly, the whole approach needs rethinking, not patching.
2. **Vendoring decision.** Getting `cr2_importer`'s four-tier canonical map (`ShapeKeyImporter._build_canonical_name_map()` — BODY valueParm ERC link, then cross-actor targetGeom ERC link, then `p`-prefix, then raw name) means parsing CR2 channel/ERC data, which means bringing a trimmed `cr2_parser.py`/`poser_io.py` into `poser_tools`. `poser_tools` already vendors `io_scene_fbx` as its own copy (see `poser_tools/vendor/`), so the precedent is to vendor rather than share code between the two add-ons — but confirm with the project owner before duplicating parser code, in case a shared library is preferred instead.

Do not start implementation on this phase without both unknowns resolved and a plan approved — per this repo's phased-work policy, this one clearly crosses the "architectural change touching core logic" threshold.

---

## Summary of open decisions for the project owner

- ~~Phase 2 (diagnostics): `core/` package now, or keep flat?~~ Done — shipped on `feature/shapekey-diagnostics`.
- ~~Phase 1: user-facing JCM toggle or always-skip?~~ Done — always skip, no toggle; `feature/jcm-exclusion`.
- ~~Phase 1: does `startswith('JCM')` survive FBX export?~~ Yes — verified against LaFemme / Aiko3 / Kira.
- Phase 5: vendor a trimmed CR2 parser into `poser_tools`, or extract a shared library both add-ons depend on?
- Phase 5: `.cr2` + matching `.fbx` pairs — owner has generated files; `~/Desktop/{LaFemme,Aiko3,Kira}.fbx` have morphs (binary FBX 7500), `../FBX_Weightmap_Tests/*.cr2` are the CR2 sources for the Legacy set (but those FBX exports carry no morphs).
