# Shape-Key Handling Improvements — record

Four phases shipped; Phase 5 (CR2 cross-reference) is **reopened** — the first ruling was measured
against an incomplete test CR2 and didn't hold up against a proper dataset. The per-phase sections
and the decisions log are the reference for anyone touching `core/functionsShapeKeys.py`. Read
`../CLAUDE.md` first for layout and design notes.

## Why this exists

`poser_tools`' FBX importer consolidates Poser's split parent/child morphs (a full-body morph plus per-body-part deltas that FBX export leaves as separate shape keys) into single usable shapekeys — see `poser_tools/core/functionsShapeKeys.py`, driven by `poser_tools/operators/fixPoserShapekeys.py`.

A sibling private repo, `cr2_importer` (a.k.a. "Poser Bridge", `PsyDreamer-3D/cr2_importer`), solves a related problem from a stronger position: it parses Poser's `.cr2` files directly, so it has access to the actual authored channel relationships (ERC / `valueOpDeltaAdd` links) that FBX export discards. Comparing the two surfaced five concrete improvements for `poser_tools`. Original priority was
JCM-first, but the work order was changed to put diagnostics first — Phases 1, 3 and 4 all report
through the same channel, and diagnostics is the only phase not blocked on real Poser test data:

1. **Diagnostics** via `self.report()` + a `bpy.data.texts` block, replacing `print()` — **done** (`feature/shapekey-diagnostics`)
2. **JCM detection/exclusion** — **done** (`feature/jcm-exclusion`) — JCM keys are *deleted*
3. **Overlap-safe delta accumulation** — **done** (`feature/overlap-detection`) — detection-only tripwire; real Poser splits are disjoint
4. **Round-trip merge metadata** — **done** (`feature/merge-metadata`) — `obj.data['poser_shapekey_merges']` JSON
5. Optional CR2 cross-reference for ground-truth canonical naming — **reopened, parser fixed,
   name matcher shipped** — `CR2Parser` lives in `core/cr2/`, naming coverage is **100%** against
   a complete CR2, and `name_match.py` resolves an FBX shape-key name to its full per-actor
   channel group — see Phase 5. Whether/how to act on it (replace or augment the heuristic) is
   still open.

Phases 1–4 are done; see their sections for what shipped. Phase 5 has real infrastructure now
(`core/cr2/`: parsing + name matching) but no decided design for using it in the add-on yet.

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

## Phase 5 — CR2 cross-reference — **REOPENED, parser now in-house**

**Goal:** use the source `.cr2`/`.crz` to get a ground-truth `internal_name → canonical_name` map
instead of guessing from FBX shape-key name patterns. Doubled as the first step in bringing usable
parts of `cr2_importer` over to `poser_tools` — done: `CR2Parser` now lives in `core/cr2/`.

### First spike (ruled out) — and why that ruling didn't hold up

Originally spiked against `~/Desktop/{LaFemme,Aiko3}.fbx` + `LaFemme Pro.cr2` /
`Legacy-Aiko3.cr2` (a path that no longer exists — see "re-spike" below). Verdict then: ruled out,
three blockers. Blocker #1 ("the CR2 usually doesn't contain the morphs") turned out to be a
**test-data artifact**: `Legacy-Aiko3.cr2` was a minimal/stripped CR2 (18 `targetGeom` channels
total), not representative of a real base-figure file.

### Re-spike with `Test_Poser_Assets/` — reopened

Owner supplied a proper dataset: `!Aiko 3.cr2` (inline deltas) ↔ `Aiko3.fbx` /
`Aiko3 SP All Morphs.fbx` (includes injected Stephanie 3 morphs), `Aiko3 All SP Morphs.cr2`
(external deltas via `.pmd`) ↔ the same, `LaFemme Pro tmp.cr2` + `.pmd` ↔ `LaFemme.fbx` /
`LaFemme with 3rd Party Morphs.fbx`. Same method as before (`CR2Parser` + vendored `parse_fbx`,
diffing FBX shape-key names against CR2 `targetGeom` `internal_name`/`display_name`):

| FBX | CR2 | shape keys | matched | rate |
|---|---|--:|--:|--:|
| `Aiko3.fbx` | `!Aiko 3.cr2` | 637 | 620 | 97.3% |
| `Aiko3 SP All Morphs.fbx` | `Aiko3 All SP Morphs.cr2` | 1193 | 1176 | 98.6% |
| `LaFemme.fbx` | `LaFemme Pro tmp.cr2` | 300 | 271 | 90.3% |
| `LaFemme with 3rd Party Morphs.fbx` | `LaFemme Pro tmp.cr2` | 671 | 619 | 92.2% |

Against a complete base-figure CR2, coverage is 90–99%, not 2.8%. Also confirmed directly (not
guessed): the small `Aiko3 All SP Morphs.cr2` (3.1MB, *more* targetGeom channels than the 48MB
`!Aiko 3.cr2`) is small because it externalizes deltas to a `.pmd` — `morphBinaryFile
:Runtime:libraries:Character:DAZ Aiko 3:Aiko3 All SP Morphs.pmd` appears twice in the raw text —
**not** a `.pz2` injection reference. Channel *declarations* (name, hierarchy, ERC) stay in the
CR2 text either way; only the per-vertex delta payload moves to the `.pmd`. So blocker #1 is not
real for a complete CR2 — the earlier ruling conflated "test file was incomplete" with "this
approach doesn't work."

**Blocker #2 (naming inconsistency) — still real.** Which tier wins is figure-dependent: Aiko3
mostly matches `display_name` (407 + 953 across the two exports), LaFemme mostly matches
`internal_name` (237, 584). No fixed rule; a real matcher needs both tiers tried in some order.

**Blocker #3 (parser gaps) is now the dominant, and the only fixable, remaining gap.** Checked
every miss by hand against the raw CR2 text. With one exception (`GMThumbMorph` — genuinely
absent, real added content), **100% of the misses are `targetGeom` channels whose internal name
contains a space** — `targetGeom Blink Right`, `targetGeom Toes Grasp`, `targetGeom Thumb Morph`,
etc. — present in the file, dropped by `cr2_importer`'s parser.

### The parser bug — found, brought in-house, fixed

`cr2_importer` is an unmaintained project (owner's call), so rather than fix this upstream, its
`CR2Parser` (+ `constants.py`, `poser_io.py` — its only two deps, both pure stdlib) moved into
`poser_tools/core/cr2/`, actively maintained here from now on. See `CLAUDE.md` for why this isn't
`vendor/` (that name means frozen; this code gets extended, starting with this fix).

`CR2Parser._parse_channel()` read a channel's `internal_name` as a single token:
```python
def _parse_channel(self) -> Channel:
    kind = self.consume()
    internal_name = '' if self.peek() == '{' else self.consume()   # <-- one token only
    ch = Channel(internal_name=internal_name, kind=kind)
    if self.peek() != '{':
        return ch                                                  # <-- bails here
    ...
```
For `targetGeom Blink Right\n\t{...}`: `internal_name = "Blink"` (one `consume()`), then `peek()`
is `"Right"` — not `"{"` — so the function returns immediately with a **truncated** name and no
body at all (no `display_name`, no `deltas`, no ERC). The orphaned `"Right"` token and the
channel's real `{` then get silently swallowed by the caller's fallback token handling. The whole
channel — deltas included — is lost, not just under-labeled. Same grammar problem the tokenizer
already solves for Poser's `name` keyword (unquoted, can contain spaces); channel `internal_name`
just needed the same treatment.

**Fixed** in `core/cr2/cr2_parser.py`: consume tokens until `{` instead of one token, joined with
spaces. Covered by `tests/test_cr2_parser.py` (inline-snippet unit tests for the space case, the
bare-nameless-channel case, and the "doesn't swallow the next channel" case, plus fixture tests
against all three real CR2s asserting the previously-dropped names are now present).

**Re-spiked with the fix in place** — same four FBX/CR2 pairs, same method:

| FBX | CR2 | shape keys | matched | rate |
|---|---|--:|--:|--:|
| `Aiko3.fbx` | `!Aiko 3.cr2` | 637 | 637 | **100%** |
| `Aiko3 SP All Morphs.fbx` | `Aiko3 All SP Morphs.cr2` | 1193 | 1193 | **100%** |
| `LaFemme.fbx` | `LaFemme Pro tmp.cr2` | 300 | 300 | **100%** |
| `LaFemme with 3rd Party Morphs.fbx` | `LaFemme Pro tmp.cr2` | 671 | 671 | **100%** |

Zero `NO_MATCH` across all four — including `GMThumbMorph`, which the first re-spike had pegged as
genuinely-new content not in the CR2. It wasn't: it was another casualty of the same bug (a
space-containing sibling channel corrupting the parse), recovered once the fix landed. **The
entire residual gap from the first re-spike was this one bug** — there is no remaining
data-availability blocker for a complete base-figure CR2.

**Blocker #2 (naming inconsistency) still stands** — `internal_name` vs `display_name` dominance
is still figure-dependent (compare the `tgeom_internal`/`tgeom_display` split above across the four
pairs) — any real matcher still needs both tiers, tried in some order, no fixed rule for which wins.

### The name matcher — blocker #2, resolved

`core/cr2/name_match.py`: `build_channel_index(figure)` + `match_channel_group(index, name)`.

**Structural finding while building this:** a morph name almost never identifies a single
`Channel`. Poser declares a body-wide morph (`Pregnant`, `PBMFullFigure`, …) **once per actor** —
hip, chest, every finger, … — up to 50+ separate `Channel`s sharing one name in the real test
CR2s, each holding that actor's slice of the full mesh delta. So the matcher's unit of work is
name → **channel group**, not name → one channel. (This mirrors, on the CR2 side, exactly the
per-actor split `core/functionsShapeKeys.py`'s heuristic already reconstructs from FBX `.001`/
`.002` naming.)

**Also checked:** no `display_name` is shared by two *distinct* `internal_name`s within a figure
(0 collisions across all three CR2s) — confirmed even where a name's *own* display_name disagrees
across actors of the same group (Aiko3's `HdStylized`: `display_name` is `"HdStylized"` on the
`head` actor but `"pHdStylized"` on `neck`/eye actors — same `internal_name`, same group, still
resolves correctly either way). So blocker #2's "which tier wins" needed no priority/scoring —
`by_internal` first, `by_display → by_internal` as a safe fallback.

**Verified against real data:** re-ran the matcher over every FBX shape-key name from the Phase 5
re-spike (LaFemme, LaFemme 3rd-party, Aiko3 SP — all previously 100%-matched) — every name
resolves to a non-empty channel group. One instructive exception: on `Aiko3.fbx` ↔ `!Aiko 3.cr2`,
56 names (`Realistic`, `Stylized`, `FullFigure`, `Muscular`, …) resolve to `[]` — **by design**,
not a bug. Those are `valueParm` master-dial channels (the un-prefixed name), not `targetGeom`
morph channels — the actual geometry for that morph lives on differently-named (`pRealistic`/
`PBMRealistic`) `targetGeom` channels, which the matcher does resolve, just under their own
group. `name_match.py` only indexes `targetGeom` (the kind that carries deltas) — a `valueParm`
hit means "this name isn't a morph's own geometry channel," a real and useful distinction, not a
gap to close.

### Open decisions

- ~~Vendor a trimmed, fixed parser, or shared library?~~ Resolved — vendored into `core/cr2/`,
  actively maintained there (`cr2_importer` won't be touched again for this).
- ~~The 2-tier `internal_name`/`display_name` matcher~~ Resolved — `core/cr2/name_match.py`,
  `tests/test_cr2_name_match.py`.
- Still open: whether/how any of this replaces or augments the current heuristic in
  `core/functionsShapeKeys.py`. Matching + full naming coverage remove the data and naming
  blockers, but don't by themselves answer whether cross-referencing the CR2 is worth wiring into
  the add-on's actual import flow (needs combining a channel group's per-actor deltas into one
  mesh-wide delta — the way `cr2_importer`'s `ShapeKeyImporter._collect_morphs()` did it — plus a
  file picker, handling for a missing/mismatched CR2, etc.). No design committed.
- **The real motivating goal turned out to be bigger than this**, and now has its own document:
  `docs/handoff-morph-injection.md` — importing a 3rd-party morph *package* and adding new shape
  keys to an already-imported mesh, not just grouping existing ones. Vertex correspondence between
  Poser-native geometry and the FBX-imported mesh (the previously-unsolved piece) has been
  investigated there and found solvable.

Spike script: `scratchpad/spike_cr2_names.py` (not committed; session-local under `/tmp`), now
importing `poser_tools.core.cr2.cr2_parser` instead of `cr2_importer`.

---

## Phase 6 — Auto-detect legacy Daz3D naming — **DONE**

The "Legacy Daz3D Figure" checkbox required the user to already know which Poser figure an FBX
came from — the FBX itself carries no figure identity, so this was pure user knowledge, not
anything the add-on could check. Owner wanted it automatic.

**Real signal found, no figure lookup needed.** `is_child_shapekey()`'s existing `is_daz` branch
already knows the tell: M3/M4-era Daz morphs are named with a `p`/`PBM` prefix
(`pStylized`, `PBMFullFigure`) instead of Blender's `.NNN` dedup suffix. That prefix is present in
the *raw*, pre-consolidation shape-key list — sniffing it directly turns out to need nothing more
than the FBX Blender already imported. Verified against real FBX exports in `Test_Poser_Assets/`:

| | LaFemme (modern) | Aiko3 (legacy Daz M3/M4) |
|---|--:|--:|
| shape keys | 300 | 637 |
| `p[A-Z]…` / `PBM…`-prefixed | 0 | 410 |

No gray zone in the data tested — a tighter regex than the existing per-morph check
(`^p[A-Z]`, not just `sh_name[0] == 'p'`) avoids a coincidental modern-figure morph name (e.g. an
English word starting with lowercase "p") tripping detection; `_LEGACY_DAZ_MIN_MATCHES = 3` is a
floor against one stray name, not a tuned cutoff — real figures land at 0 or in the hundreds.

**Implemented:**
- `core/functionsShapeKeys.py`: `detect_legacy_daz(shapekeys)` counts `p[A-Z]`/`PBM`-prefixed raw
  names; `resolve_legacy_daz(mode, shapekeys, log)` turns an `'AUTO'`/`'ON'`/`'OFF'` mode into the
  bool the rest of the module expects, logging which path was taken.
  `consolidate_poser_shapekeys()`'s third parameter changed from a bare `_is_daz` bool to
  `legacy_daz_mode`, resolved once at the top of the function.
- Both call sites — the import dialog (`operators/importPoserFBX.py`) and the manual re-run panel
  (`operators/fixPoserShapekeys.py` / `properties/poserToolsAddonSettings.py`) — swapped their
  `BoolProperty` for an `EnumProperty` (`AUTO` / `ON` / `OFF`, default `AUTO`). Kept as a genuine
  override rather than removing it outright: Poser content spans 20 years of community assets, and
  a heuristic with no escape hatch is a worse failure mode than one extra rarely-touched dropdown.
- The resolution decision is always logged (`"Legacy Daz3D naming: auto-detected yes/no"` or
  `"forced on/off"`) to the existing report text block — same transparency pattern as the
  overlap tripwire (Phase 3), not a silent guess.

**Verified in headless Blender** (real FBX import → `consolidate_poser_shapekeys`, `AUTO` mode):
LaFemme detected `no` — 21 merges / 105 children, identical to the pre-change manual-checkbox
result. Aiko3 detected `yes` — 84 merges / 419 children, identical to the pre-change result.
Forcing `OFF` on Aiko3 as a sanity check undercounts as expected (64 merges / 344 children,
17 fewer empty-morph skips) — confirms detection is actually doing the work, not a no-op.

---

## Decisions log

- ~~Phase 2 (diagnostics): `core/` package now, or keep flat?~~ Done — shipped on `feature/shapekey-diagnostics`.
- ~~Phase 1: user-facing JCM toggle or always-skip?~~ Done — always skip, no toggle; `feature/jcm-exclusion`.
- ~~Phase 1: JCM keys — leave in place or delete?~~ Delete — FBX drops the driver so a baked JCM shape can't fire.
- ~~Phase 1: does `startswith('JCM')` survive FBX export?~~ Yes — verified against LaFemme / Aiko3 / Kira.
- ~~Phase 3: overlap-safe accumulation — fix or detect?~~ Detect only — real Poser splits are disjoint (probed).
- Phase 5: "the CR2 rarely has the morphs" — **reversed.** That was true of the specific `Legacy-Aiko3.cr2`
  test file (18 channels, a minimal/stripped CR2), not of real base-figure CR2s (90–99% coverage
  against `!Aiko 3.cr2`, `Aiko3 All SP Morphs.cr2`, `LaFemme Pro tmp.cr2`). Phase 5 reopened.
- ~~Phase 5: vendor a trimmed CR2 parser, or shared library?~~ Vendored into `core/cr2/` — `cr2_importer`
  is unmaintained, so this is the parser's new home, actively maintained here.
- ~~Phase 5: does the parser drop space-in-name channels?~~ Yes, confirmed and **fixed** in
  `core/cr2/cr2_parser.py`. Re-spike after the fix: 100% naming coverage on all four FBX/CR2 pairs
  (was 90–98.6%) — the entire residual gap was this one bug, not missing data.
- ~~Phase 5: does "which tier wins" (internal_name vs display_name) need priority rules?~~ No —
  0 display_name collisions with distinct internal_names across all three CR2s, so a simple
  internal-then-display fallback (`core/cr2/name_match.py`) is safe.
- Phase 5: a morph *name* maps to a **group** of `Channel`s (one per actor), not a single
  `Channel` — up to 50+ in the real test CR2s. Any future code building actual shape-key deltas
  from CR2 data needs to combine a group's per-actor deltas via global vertex-index translation
  (`cr2_importer`'s `ShapeKeyImporter._collect_morphs()` is the reference pattern) — not done here.

Test data: `Test_Poser_Assets/` (see project memory / ask the owner for the current path — it has
moved once already). Blender is runnable headless (`/snap/bin/blender`).
