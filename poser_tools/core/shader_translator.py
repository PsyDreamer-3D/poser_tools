# SPDX-License-Identifier: MIT
"""
shader_translator.py
--------------------
Converts a PoserShaderTree (parsed by cr2_parser) into a Blender Cycles node
tree on an existing bpy.types.Material.

Adapted from cr2_importer (PsyDreamer-3D's own, now unmaintained CR2 importer),
core/shader_translator.py, for Phase 8 MAT poses (docs/handoff-morph-injection.md).
MIT like the rest of the adapted code; maintained here from now on. Changes: the
parser import path, and node types it has no mapping for are now reported (through
`reporter`) instead of skipped silently. It uses bpy, so it lives in core/, not core/cr2/.

Entry point
-----------
    build_shader_tree(bl_mat, shader_tree, load_image_fn, reporter=None)

Scope
-----
- Only nodes reachable from the shaderTree's superflyRoot (Cycles path) are
  created.  fireflyRoot-only nodes are silently skipped.
- Sub-BSDF nodes connected via AlternateSpecular/AlternateDiffuse are
  collapsed into the parent Principled BSDF's Coat and Subsurface sockets
  rather than emitting disconnected shader nodes.
- Bump/NormalMap inputs are routed through ShaderNodeBump/ShaderNodeNormalMap
  before connecting to the Principled BSDF Normal socket.
- Unknown Poser node types are skipped and reported once per material.

Poser value tuple convention
-----------------------------
Parser stores (current, min, max) for scalar inputs and (R, G, B) for colour
inputs.  _coerce_value() inspects the Blender socket type and picks the right
representation automatically.
"""

from __future__ import annotations
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .cr2.cr2_parser import PoserShaderTree, PoserShaderNode


# ---------------------------------------------------------------------------
# Operation tables
# ---------------------------------------------------------------------------

# Poser math_functions Math_Argument index → Blender ShaderNodeMath.operation
_MATH_OPS: tuple[str, ...] = (
    'ADD', 'SUBTRACT', 'MULTIPLY', 'DIVIDE',
    'SINE', 'COSINE', 'TANGENT', 'ARCSINE', 'ARCCOSINE', 'ARCTANGENT',
    'MINIMUM', 'MAXIMUM', 'POWER', 'EXPONENT', 'LOGARITHM',
    'SQRT', 'ABSOLUTE', 'FLOOR', 'CEIL', 'ROUND', 'MODULO',
)

# Poser color_math Math_Argument index → Blender ShaderNodeMix.blend_type
_COLOR_MATH_OPS: tuple[str, ...] = (
    'MULTIPLY', 'ADD', 'SUBTRACT', 'DIVIDE', 'MIX',
)


# ---------------------------------------------------------------------------
# Node mapping table
# ---------------------------------------------------------------------------
# Format:
#   { poser_node_type: (bl_idname, {poser_input_key: bl_socket_name_or_int}) }
#
# Socket key rules:
#   str  → socket looked up by name in bl_node.inputs
#   int  → socket accessed by index (for nodes with duplicate "Value" names)
#   None → input is explicitly skipped (no Blender equivalent in this phase)
#
# Types absent from this dict are skipped and named in a warning (build_shader_tree's `skipped`).

POSER_NODE_MAP: dict[str, tuple[str, dict]] = {
    # Classic Poser surface (Firefly renderer)
    'poser': ('ShaderNodeBsdfPrincipled', {
        'Diffuse_Color':    'Base Color',
        'Highlight_Value':  'Specular IOR Level',
        'Roughness':        'Roughness',
        'Transparency_Max': 'Alpha',            # inverted — see _ALPHA_INPUTS
        'Ambient_Color':    'Emission Color',
        'Ambient_Value':    'Emission Strength',
        # Bump, AlternateSpecular, AlternateDiffuse: handled by collapse pass
    }),
    # Poser PBR surface (Superfly / Firefly)
    'PhysicalSurface': ('ShaderNodeBsdfPrincipled', {
        'Color':            'Base Color',
        'Specular':         'Specular IOR Level',
        'Roughness':        'Roughness',
        'Metallic':         'Metallic',
        'Transparency':     'Alpha',            # inverted — see _ALPHA_INPUTS
        'Emission':         'Emission Color',
        'EmissionStrength': 'Emission Strength',
        # Bump, NormalMap, AlternateSpecular, AlternateDiffuse: collapse pass
        'Bump':     None,
        'NormalMap': None,
    }),
    # Image texture — image loaded in _post_init_node; UV scaling deferred.
    'image_map': ('ShaderNodeTexImage', {}),
    # Blend/mix (colour or shader).
    # ShaderNodeMix exposes all data-type variants simultaneously; name-based
    # lookup of 'A'/'B' returns the VALUE sockets (index 2/3), not the RGBA
    # ones.  Use explicit indices: 0=Factor(float), 6=A(RGBA), 7=B(RGBA).
    # RGBA output is at index 2 (not 0); see _rgba_output_of().
    'blender': ('ShaderNodeMix', {
        'Input_1':  6,          # A (RGBA) — data_type set to 'RGBA' in _post_init_node
        'Input_2':  7,          # B (RGBA)
        'Blending': 'Factor',   # Factor (VALUE, index 0) — name lookup is unambiguous
    }),
    # Colour math (blend_type set from Math_Argument in _post_init_node)
    'color_math': ('ShaderNodeMix', {
        'Value_1': 6,   # A (RGBA)
        'Value_2': 7,   # B (RGBA)
    }),
    # Scalar math (operation set from Math_Argument in _post_init_node)
    'math_functions': ('ShaderNodeMath', {
        'Value_1': 0,   # inputs[0] — ShaderNodeMath has two "Value" sockets
        'Value_2': 1,   # inputs[1]
    }),
    # HSV adjustment
    'hsv2': ('ShaderNodeHueSaturation', {
        'Color':      'Color',
        'Hue':        'Hue',
        'Saturation': 'Saturation',
        'Value':      'Value',
    }),
    # Fresnel blend — only the IOR scalar; Principled handles Fresnel via IOR.
    'fresnel_blend': ('ShaderNodeFresnel', {
        'Index_of_Refraction': 'IOR',
    }),
    # Specular highlight types — all map to ShaderNodeBsdfAnisotropic.
    # ShaderNodeBsdfGlossy was merged into ShaderNodeBsdfAnisotropic in Blender
    # 5.0 and is absent from the documented Python API (bpy.types).
    # When these appear as AlternateSpecular on a poser/PhysicalSurface node
    # they are collapsed into the parent Principled BSDF Coat layer instead.
    'glossy': ('ShaderNodeBsdfAnisotropic', {
        'Specular_Color': 'Color',
        'Roughness':      'Roughness',
    }),
    'specular': ('ShaderNodeBsdfAnisotropic', {
        'Specular_Color': 'Color',
        'Roughness':      'Roughness',
    }),
    'ks_microfacet': ('ShaderNodeBsdfAnisotropic', {
        'Specular_Color': 'Color',
        'roughness':      'Roughness',
    }),
    'blinn': ('ShaderNodeBsdfAnisotropic', {
        'Specular_Color': 'Color',
        'Eccentricity':   'Roughness',
    }),
    # Anisotropic specular (full input set) — Principled Anisotropic [15,16]
    'aniso': ('ShaderNodeBsdfAnisotropic', {
        'Specular_Color': 'Color',
        'u_Roughness':    'Roughness',
        'Rotation':       'Rotation',
    }),
    # Subsurface scattering — collapsed into Principled Subsurface [8,10]
    # when connected via AlternateDiffuse on a poser/PhysicalSurface node.
    'scatter': ('ShaderNodeSubsurfaceScattering', {
        'Color': 'Color',
        'scale': 'Scale',
    }),
    # Ray-traced mirror reflection
    'reflect': ('ShaderNodeBsdfAnisotropic', {
        'Diffusion': 'Roughness',
    }),
    # Procedural noise
    'turbulence': ('ShaderNodeTexNoise', {
        'Octaves': 'Detail',
    }),
    'fbm': ('ShaderNodeTexNoise', {
        'Octaves':       'Detail',
        'Frequency_Gap': 'Distortion',
    }),
    # Channel extraction (output socket chosen from Component value)
    'comp': ('ShaderNodeSeparateColor', {
        'Color': 'Color',
    }),
    # Compound translations (_post_init_node builds the helper nodes and routes the inputs that
    # live on them; the node here is the one whose output the rest of the tree uses). Poser's
    # meaning of each input is from the Poser 11 Reference Manual, chapter 16.
    # Ambient Occlusion: white mixed toward Blender's AO colour by Strength ("the degree to which
    # the node will darken occluded surfaces"); MaxDist -> Distance, Samples -> samples. RayBias
    # has no counterpart (Poser's own SuperFly ignores it too).
    'ambient_occlusion': ('ShaderNodeMix', {
        'Strength': 'Factor',
    }),
    # Clouds: Sky_Color mixed toward Cloud_Color by a fractal Noise Texture. Complexity -> Detail;
    # Scale ("the size of the clouds") inverted into Blender's Scale, which is a frequency. Bottom,
    # Bias and Gain have no direct counterpart; the pattern is an approximation, as any procedural
    # noise translated between renderers is.
    'clouds': ('ShaderNodeMix', {
        'Sky_Color':   6,   # A (RGBA)
        'Cloud_Color': 7,   # B (RGBA)
    }),
    # Color Ramp: four colours, a "spline color map" looked up by Input. Built as a Blender Color
    # Ramp, or as a chain of colour mixes when a colour is fed by another node (_colorramp_entry).
    'colorramp': ('ShaderNodeValToRGB', {
        'Input': 0,         # Fac
    }),
}

# A Color Ramp's stops can't take links, but on the owner's library 533 colour inputs of 874 ramps
# are fed by another node; such a ramp is built from mixes instead, linear between the colours.
_RAMP_COLORS = ('Color1', 'Color2', 'Color3', 'Color4')
_COLORRAMP_CHAIN = ('ShaderNodeMix', {})
# Poser's AO MaxDist is read as inches: its documented meaning is a distance, and the values found on
# the owner's library (39.37 = 1 m in inches, 7.74, 5.16, 0.74) only make physical sense in inches;
# as Poser native units 7.74 would be ~20 m. An inference, not documented by Poser.
_AO_DISTANCE_PER_UNIT = 0.0254


def _entry_for(poser_node):
    """(bl_idname, input_map) for a Poser node, or None when it has no translation."""
    if poser_node.node_type == 'colorramp' and any(
            (inp := poser_node.inputs.get(key)) is not None and inp.node_ref for key in _RAMP_COLORS):
        return _COLORRAMP_CHAIN
    return POSER_NODE_MAP.get(poser_node.node_type)

# Inputs whose current value[0] must be inverted before assigning to Blender.
# Poser transparency: 0 = opaque, 1 = transparent.
# Blender Alpha:      1 = opaque, 0 = transparent.
_ALPHA_INPUTS: frozenset[tuple[str, str]] = frozenset({
    ('poser', 'Transparency_Max'),
    ('PhysicalSurface', 'Transparency'),
})

# Blender node idnames that produce a Shader output (connect to Output.Surface).
# ShaderNodeBsdfGlossy omitted — merged into ShaderNodeBsdfAnisotropic.
_SHADER_IDNAMES: frozenset[str] = frozenset({
    'ShaderNodeBsdfPrincipled',
    'ShaderNodeBsdfAnisotropic',
    'ShaderNodeSubsurfaceScattering',
    'ShaderNodeMixShader',
    'ShaderNodeAddShader',
    'ShaderNodeEmission',
})

# Sub-BSDF Poser types that collapse into the parent Principled BSDF when
# connected via AlternateSpecular (→ Coat layer) or AlternateDiffuse (→ SSS).
_COLLAPSIBLE_SPEC_TYPES: frozenset[str] = frozenset({
    'blinn', 'glossy', 'specular', 'aniso', 'ks_microfacet', 'reflect',
})
_COLLAPSIBLE_DIFF_TYPES: frozenset[str] = frozenset({'scatter'})


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

def build_shader_tree(bl_mat, shader_tree, load_image_fn, reporter=None):
    """
    Translate a PoserShaderTree into a Blender Cycles node tree on bl_mat.

    Parameters
    ----------
    bl_mat : bpy.types.Material
        Target Blender material.  Its node_tree is cleared and rebuilt.
    shader_tree : PoserShaderTree
        Parsed Poser shader graph from cr2_parser.
    load_image_fn : callable(str) -> bpy.types.Image | None
        Resolves a Poser colon-path to a Blender Image data-block.
        Pass MaterialMixin._load_image bound to the importer instance.
    reporter : callable({'INFO'|'WARNING'}, str) | None
        Forwarded self.op.report for diagnostic messages.  May be None.
    """
    nodes = bl_mat.node_tree.nodes
    links = bl_mat.node_tree.links
    nodes.clear()

    # Build fast lookup for Poser nodes and determine which are reachable from
    # the active renderer root (superfly = Cycles; firefly = Firefly/legacy).
    # Nodes reachable only from firefly_root are skipped to avoid creating a
    # parallel disconnected graph for Blender's Cycles renderer.
    poser_nodes_by_name: dict = {n.node_name: n for n in shader_tree.nodes}
    root_name = shader_tree.superfly_root or shader_tree.firefly_root
    _reachable = _find_reachable(root_name, poser_nodes_by_name) if root_name else None

    # Identify sub-BSDF nodes that will be collapsed into their parent
    # Principled BSDF rather than emitted as separate disconnected shader nodes.
    # A node qualifies when it is connected via AlternateSpecular or
    # AlternateDiffuse to a poser/PhysicalSurface root node.
    _collapsed: set[str] = set()
    for pn in shader_tree.nodes:
        if pn.node_type not in ('poser', 'PhysicalSurface'):
            continue
        for inp_name, inp in pn.inputs.items():
            if not inp.node_ref:
                continue
            ref = poser_nodes_by_name.get(inp.node_ref)
            if ref is None:
                continue
            if inp_name == 'AlternateSpecular' and ref.node_type in _COLLAPSIBLE_SPEC_TYPES:
                _collapsed.add(inp.node_ref)
            elif inp_name == 'AlternateDiffuse' and ref.node_type in _COLLAPSIBLE_DIFF_TYPES:
                _collapsed.add(inp.node_ref)

    out_node = nodes.new('ShaderNodeOutputMaterial')

    # Poser node_name → created Blender node
    bl_nodes: dict = {}
    # Poser node_name → {input name: [(Blender node, socket key)]} for inputs that live on a compound
    # translation's helper nodes rather than on the node in bl_nodes (_post_init_node).
    bl_routes: dict = {}

    # Tracks whether any surface node maps transparency so blend_method is set.
    has_transparency_input = False
    # Poser node types with no entry in POSER_NODE_MAP, reached from the root: {type: count}.
    skipped: dict = {}

    # ------------------------------------------------------------------
    # Phase 1: create nodes, run per-type setup, then assign constant values.
    # Per-type setup (e.g. data_type on ShaderNodeMix) must run before socket
    # value assignment so that socket names resolve correctly.
    # ------------------------------------------------------------------
    for i, poser_node in enumerate(shader_tree.nodes):
        # Only create nodes reachable from the superfly/firefly root.
        if _reachable is not None and poser_node.node_name not in _reachable:
            continue
        # Sub-BSDF nodes get folded into their parent Principled (collapse pass).
        if poser_node.node_name in _collapsed:
            continue

        entry = _entry_for(poser_node)
        if entry is None:
            # Said, not silent: a skipped node breaks its chain, so the material can come out
            # far plainer than in Poser (Poser's `toon` node: a toon MAT ends up a bare Principled).
            skipped[poser_node.node_type] = skipped.get(poser_node.node_type, 0) + 1
            continue

        bl_idname, input_map = entry
        bl_node = nodes.new(bl_idname)
        # Simple grid layout: 3 columns
        col = i % 3
        row = i // 3
        bl_node.location = (col * 300, -row * 280)
        bl_node.name  = poser_node.node_name
        bl_node.label = poser_node.node_name
        bl_nodes[poser_node.node_name] = bl_node

        # Per-type setup before values (sets enums, data_type, loads image, builds helper nodes)
        bl_routes[poser_node.node_name] = _post_init_node(
            bl_node, poser_node, load_image_fn, reporter, nodes, links) or {}

        # image_map nodes that failed to load must be removed: an empty
        # ShaderNodeTexImage outputs the magenta error colour in Cycles (black
        # in Eevee), which would poison every socket it is connected to.
        if bl_idname == 'ShaderNodeTexImage' and getattr(bl_node, 'image', None) is None:
            nodes.remove(bl_node)
            del bl_nodes[poser_node.node_name]
            continue

        # Assign constant default values
        for inp_name, inp in poser_node.inputs.items():
            if inp.value is None:
                continue
            for target, bl_socket_key in _targets(bl_node, input_map, bl_routes[poser_node.node_name], inp_name):
                _set_socket_value(target, bl_socket_key,
                                  inp.value, poser_node.node_type, inp_name)

        # Detect whether this node will drive an Alpha input so we can enable
        # transparency blending on the material after the tree is wired.
        if (node_type := poser_node.node_type) in ('poser', 'PhysicalSurface'):
            transparency_key = 'Transparency_Max' if node_type == 'poser' else 'Transparency'
            inp = poser_node.inputs.get(transparency_key)
            if inp is not None:
                if inp.node_ref or (inp.value and inp.value[0] > 0.0):
                    has_transparency_input = True

    # ------------------------------------------------------------------
    # Collapse pass: fold sub-BSDF nodes into their parent Principled BSDF
    # and wire Bump/NormalMap inputs through the appropriate normal node.
    # Runs after Phase 1 so all bl_nodes are registered, but before Phase 2
    # so the resulting links exist when Phase 2 looks for further wiring.
    # ------------------------------------------------------------------
    for poser_node in shader_tree.nodes:
        if poser_node.node_type not in ('poser', 'PhysicalSurface'):
            continue
        bl_target = bl_nodes.get(poser_node.node_name)
        if bl_target is None:
            continue

        # AlternateSpecular → Principled Coat layer
        coat_inp = poser_node.inputs.get('AlternateSpecular')
        if coat_inp and coat_inp.node_ref in _collapsed:
            sub = poser_nodes_by_name[coat_inp.node_ref]
            _collapse_into_coat(bl_target, sub, bl_nodes, links)

        # AlternateDiffuse → Principled Subsurface layer
        sss_inp = poser_node.inputs.get('AlternateDiffuse')
        if sss_inp and sss_inp.node_ref in _collapsed:
            sub = poser_nodes_by_name[sss_inp.node_ref]
            _collapse_into_sss(bl_target, sub, bl_nodes, links)

        # Bump / NormalMap → Normal socket via an intermediate normal node
        for bump_key in ('Bump', 'NormalMap'):
            bump_inp = poser_node.inputs.get(bump_key)
            if not bump_inp or not bump_inp.node_ref:
                continue
            bump_src = bl_nodes.get(bump_inp.node_ref)
            if not bump_src:
                continue

            if bump_key == 'NormalMap':
                nmap = nodes.new('ShaderNodeNormalMap')
                nmap.location = (bl_target.location[0] - 250, bl_target.location[1] - 300)
                _make_link(links, bump_src, _rgba_output_of(bump_src), nmap, 'Color')
                _make_link(links, nmap, 0, bl_target, 'Normal')
            else:
                bump_n = nodes.new('ShaderNodeBump')
                bump_n.location = (bl_target.location[0] - 250, bl_target.location[1] - 300)
                raw_str = abs(bump_inp.value[0]) if bump_inp.value else 0.005
                # Poser bump strength is in Poser units (~0.005); scale to 0–1.
                bump_n.inputs['Strength'].default_value = min(raw_str * 100.0, 1.0)
                _make_link(links, bump_src, _rgba_output_of(bump_src), bump_n, 'Height')
                _make_link(links, bump_n, 0, bl_target, 'Normal')
            break  # only one bump source per surface node

    if skipped and reporter is not None:
        reporter({'WARNING'}, "Poser shader node(s) with no Blender equivalent here, left out: "
                 + ", ".join(f"{t} x{n}" if n > 1 else t for t, n in sorted(skipped.items())))

    # ------------------------------------------------------------------
    # Phase 2: wire connections (node_ref in inputs → inter-node links).
    # comp nodes output a specific channel; all others use output[0].
    # ShaderNodeMix with data_type='RGBA' outputs colour at index 2.
    # ------------------------------------------------------------------
    comp_out: dict[str, int] = {}   # node_name → output socket index
    for poser_node in shader_tree.nodes:
        if poser_node.node_type == 'comp':
            cmp = poser_node.inputs.get('Component')
            idx = int(cmp.value[0]) if cmp and cmp.value else 0
            comp_out[poser_node.node_name] = max(0, min(2, idx))

    for poser_node in shader_tree.nodes:
        bl_target = bl_nodes.get(poser_node.node_name)
        if bl_target is None:
            continue
        entry = _entry_for(poser_node)
        if entry is None:
            continue
        _, input_map = entry

        for inp_name, inp in poser_node.inputs.items():
            if inp.node_ref is None:
                continue
            bl_source = bl_nodes.get(inp.node_ref)
            if bl_source is None:
                continue  # source type unknown/skipped/collapsed

            # Prefer the comp/channel output index; for ShaderNodeMix with
            # RGBA data_type the colour result is at index 2, not 0.
            src_idx = comp_out.get(inp.node_ref, _rgba_output_of(bl_source))
            if src_idx >= len(bl_source.outputs):
                continue

            for target, bl_socket_key in _targets(bl_target, input_map, bl_routes.get(poser_node.node_name, {}),
                                                  inp_name):
                _make_link(links, bl_source, src_idx, target, bl_socket_key)

    # ------------------------------------------------------------------
    # Phase 3: connect root surface node to material output.
    # ------------------------------------------------------------------
    root_bl = bl_nodes.get(root_name)

    if root_bl is None:
        # Fallback: use first shader-type node encountered
        for node in bl_nodes.values():
            if node.bl_idname in _SHADER_IDNAMES:
                root_bl = node
                break

    if root_bl is None:
        if reporter:
            reporter({'WARNING'},
                     f"Shader tree root '{root_name}' not found or not mapped; "
                     f"material output left unconnected.")
        return

    if root_bl.bl_idname not in _SHADER_IDNAMES:
        # Non-shader root (e.g. image texture directly as root): wrap in Emission
        emit = nodes.new('ShaderNodeEmission')
        emit.location = (out_node.location[0] - 220, 0)
        _make_link(links, root_bl, 0, emit, 'Color')
        root_bl = emit

    if root_bl.outputs:
        try:
            links.new(root_bl.outputs[0], out_node.inputs['Surface'])
        except Exception:
            pass

    # Enable transparency blending when needed so Eevee renders alpha correctly.
    if has_transparency_input:
        if hasattr(bl_mat, 'blend_method'):
            bl_mat.blend_method = 'HASHED'
        if hasattr(bl_mat, 'shadow_method'):
            bl_mat.shadow_method = 'HASHED'

    # ------------------------------------------------------------------
    # Cleanup: remove any nodes not reachable backwards from Material Output.
    # After collapsing sub-BSDFs, some intermediate nodes (e.g. the specular
    # colour chain that fed the blinn node) are created but have no downstream
    # connection to the output.  Walk every input link backwards from out_node
    # and prune everything not visited.
    # ------------------------------------------------------------------
    _used: set = set()
    _stack = [out_node]
    while _stack:
        _n = _stack.pop()
        if _n in _used:
            continue
        _used.add(_n)
        for _sock in _n.inputs:
            for _lnk in _sock.links:
                _stack.append(_lnk.from_node)

    for _n in list(nodes):
        if _n not in _used:
            nodes.remove(_n)

    # Place Material Output immediately to the right of the Principled BSDF.
    out_node.location = (root_bl.location[0] + 380, root_bl.location[1])


# ---------------------------------------------------------------------------
# Private helpers
# ---------------------------------------------------------------------------

def _find_reachable(start_name: str, nodes_by_name: dict) -> set:
    """Walk node_ref connections backwards from start_name; return all found names."""
    visited: set = set()
    stack = [start_name]
    while stack:
        name = stack.pop()
        if name in visited or name not in nodes_by_name:
            continue
        visited.add(name)
        for inp in nodes_by_name[name].inputs.values():
            if inp.node_ref:
                stack.append(inp.node_ref)
    return visited


def _rgba_output_of(bl_node) -> int:
    """
    Return the output socket index that yields an RGBA colour value.

    ShaderNodeMix exposes multiple typed 'Result' outputs simultaneously:
    index 0 = VALUE, 1 = VECTOR, 2 = RGBA, 3 = ROTATION.  When the node is
    used in RGBA mode, index 2 is the correct colour output.  All other node
    types have their colour output at index 0.
    """
    if bl_node.bl_idname == 'ShaderNodeMix':
        try:
            if bl_node.data_type == 'RGBA':
                return 2
        except AttributeError:
            pass
    return 0


def _collapse_into_coat(principled, sub_poser_node, bl_nodes, links):
    """
    Fold a blinn/glossy/specular/aniso sub-BSDF into the main Principled BSDF
    Roughness and Specular IOR Level sockets.

    Poser's AlternateSpecular node contributes highlight shape (Eccentricity)
    and intensity (Reflectivity) to the surface shading.  These map directly
    to the Principled BSDF main specular parameters.  The Coat layer is not
    used — it adds a physically distinct second Fresnel lobe that causes
    over-brightening when driven by procedural roughness chains.

    The sub-node's colour chain (Specular_Color) has no equivalent single
    input in the Principled BSDF model and is intentionally left unconnected.
    """
    nt = sub_poser_node.node_type
    for poser_key, bl_key in (
        ('Eccentricity',    'Roughness'),
        ('Roughness',       'Roughness'),
        ('u_Roughness',     'Roughness'),
        ('Reflectivity',    'Specular IOR Level'),
        ('SpecularRollOff', 'Specular IOR Level'),
    ):
        inp = sub_poser_node.inputs.get(poser_key)
        if inp and inp.value:
            _set_socket_value(principled, bl_key, inp.value, nt, poser_key)


def _collapse_into_sss(principled, sub_poser_node, bl_nodes, links):
    """
    Fold a scatter sub-BSDF into Principled BSDF Subsurface sockets.

    Principled Subsurface sockets (Blender 5.0+):
        Subsurface Weight [8]  — SSS layer strength
        Subsurface Scale  [10] — SSS radius scale in scene units
    In Blender 5.x there is no separate Subsurface Color input; SSS uses
    the same Base Color as the diffuse layer.
    """
    nt = sub_poser_node.node_type
    inp = sub_poser_node.inputs.get('scale')
    if inp and inp.value:
        _set_socket_value(principled, 'Subsurface Scale', inp.value, nt, 'scale')
    try:
        if 'Subsurface Weight' in principled.inputs:
            principled.inputs['Subsurface Weight'].default_value = 0.3
    except Exception:
        pass


def _targets(bl_node, input_map, routes, inp_name):
    """[(Blender node, socket key)] a Poser input is set or linked on: its route, else the map."""
    if inp_name in routes:
        return routes[inp_name]
    key = input_map.get(inp_name)
    return [] if key is None else [(bl_node, key)]


def _value(inputs, name, default=0.0):
    inp = inputs.get(name)
    return float(inp.value[0]) if inp is not None and inp.value else default


def _helper(nodes, idname, beside, dx=-260, dy=0):
    node = nodes.new(idname)
    node.location = (beside.location[0] + dx, beside.location[1] + dy)
    return node


def _rgba_mix(node):
    node.data_type = 'RGBA'
    return node


def _post_init_node(bl_node, poser_node, load_image_fn, reporter, nodes=None, links=None):
    """Per-type setup that must run before socket value assignment.

    Returns {Poser input name: [(Blender node, socket key)]} for inputs a compound translation
    puts on its helper nodes, or None.
    """
    nt     = poser_node.node_type
    inputs = poser_node.inputs

    if nt == 'ambient_occlusion':
        mix = _rgba_mix(bl_node)
        mix.inputs[6].default_value = (1.0, 1.0, 1.0, 1.0)     # A: unoccluded
        # Strength runs past 1 (3 and 5 on the owner's library) to darken harder: let the
        # factor extrapolate and clamp the colour instead.
        mix.clamp_factor = False
        mix.clamp_result = True
        ao = _helper(nodes, 'ShaderNodeAmbientOcclusion', mix)
        ao.samples = max(1, int(_value(inputs, 'Samples', 16)))
        max_dist = _value(inputs, 'MaxDist', 0.0)
        if max_dist > 0:
            ao.inputs['Distance'].default_value = max_dist * _AO_DISTANCE_PER_UNIT
        links.new(ao.outputs['Color'], mix.inputs[7])           # B: occluded
        return None

    if nt == 'clouds':
        mix = _rgba_mix(bl_node)
        noise = _helper(nodes, 'ShaderNodeTexNoise', mix)
        scale = _value(inputs, 'Scale', 0.0)
        if scale > 0:
            noise.inputs['Scale'].default_value = 1.0 / scale
        if _value(inputs, 'Global_Coordinates') >= 0.5:
            # World space, as Poser's Global_Coordinates on; off is the object's own space.
            geometry = _helper(nodes, 'ShaderNodeNewGeometry', noise)
            links.new(geometry.outputs['Position'], noise.inputs['Vector'])
        links.new(noise.outputs['Fac'], mix.inputs[0])
        return {'Complexity': [(noise, 'Detail')]}

    if nt == 'colorramp' and bl_node.bl_idname == 'ShaderNodeValToRGB':
        ramp = bl_node.color_ramp
        ramp.interpolation = 'CARDINAL'     # a spline through the four colours
        ramp.elements[0].position, ramp.elements[1].position = 0.0, 1.0
        ramp.elements.new(1.0 / 3.0)
        ramp.elements.new(2.0 / 3.0)
        for element, key in zip(sorted(ramp.elements, key=lambda e: e.position), _RAMP_COLORS):
            inp = inputs.get(key)
            if inp is not None and inp.value:
                element.color = (float(inp.value[0]), float(inp.value[1]), float(inp.value[2]), 1.0)
        return None

    if nt == 'colorramp':
        # A colour is fed by another node: Color1 -> Color4 through three mixes, each taking the
        # Input's third of the range (factor = clamp(3 * Input - k)). Linear, not a spline.
        final = _rgba_mix(bl_node)
        first = _rgba_mix(_helper(nodes, 'ShaderNodeMix', final, dx=-520))
        second = _rgba_mix(_helper(nodes, 'ShaderNodeMix', final, dx=-260))
        factors = []
        for k, mix in enumerate((first, second, final)):
            math = _helper(nodes, 'ShaderNodeMath', mix, dy=-220)
            math.operation = 'MULTIPLY_ADD'
            math.use_clamp = True
            math.inputs[1].default_value = 3.0
            math.inputs[2].default_value = -float(k)
            links.new(math.outputs[0], mix.inputs[0])
            factors.append((math, 0))
        links.new(first.outputs[2], second.inputs[6])
        links.new(second.outputs[2], final.inputs[6])
        return {'Input': factors,
                'Color1': [(first, 6)], 'Color2': [(first, 7)],
                'Color3': [(second, 7)], 'Color4': [(final, 7)]}

    if nt == 'image_map':
        src = inputs.get('Image_Source')
        if src and src.file:
            try:
                img = load_image_fn(src.file)
                if img is not None:
                    bl_node.image = img
            except Exception as exc:
                if reporter:
                    reporter({'WARNING'},
                             f"image_map '{poser_node.node_name}': {exc}")

    elif nt == 'math_functions':
        arg = inputs.get('Math_Argument')
        if arg and arg.value is not None:
            idx = int(arg.value[0])
            if 0 <= idx < len(_MATH_OPS):
                try:
                    bl_node.operation = _MATH_OPS[idx]
                except Exception:
                    pass

    elif nt == 'color_math':
        # Set data_type first so socket names resolve to colour variants.
        try:
            bl_node.data_type = 'RGBA'
        except Exception:
            pass
        arg = inputs.get('Math_Argument')
        if arg and arg.value is not None:
            idx = int(arg.value[0])
            if 0 <= idx < len(_COLOR_MATH_OPS):
                try:
                    bl_node.blend_type = _COLOR_MATH_OPS[idx]
                except Exception:
                    pass

    elif nt == 'blender':
        try:
            bl_node.data_type = 'RGBA'
        except Exception:
            pass


def _set_socket_value(bl_node, socket_key, value, node_type, inp_name):
    """Set bl_node socket default_value from a Poser value tuple."""
    try:
        if isinstance(socket_key, int):
            if socket_key >= len(bl_node.inputs):
                return
            socket = bl_node.inputs[socket_key]
        else:
            if socket_key not in bl_node.inputs:
                return
            socket = bl_node.inputs[socket_key]

        coerced = _coerce_value(value, socket, node_type, inp_name)
        if coerced is not None:
            socket.default_value = coerced
    except Exception:
        pass  # type mismatch or read-only socket; leave Blender default


def _coerce_value(value, socket, node_type, inp_name):
    """
    Convert Poser (v1, v2, v3) to the correct Python type for the socket.

    For scalar inputs: v1 = current value, v2 = min, v3 = max.
    For colour inputs: (v1, v2, v3) = (R, G, B).
    """
    v = value
    t = getattr(socket, 'type', 'VALUE')

    if (node_type, inp_name) in _ALPHA_INPUTS:
        # Invert Poser transparency convention: 0=opaque → 1.0 alpha
        return float(max(0.0, min(1.0, 1.0 - v[0])))

    if t == 'RGBA':
        return (float(v[0]), float(v[1]), float(v[2]), 1.0)
    if t in ('VALUE', 'FLOAT', 'INT'):
        return float(v[0])
    if t == 'VECTOR':
        return (float(v[0]), float(v[1]), float(v[2]))
    return float(v[0])  # safe fallback


def _make_link(links, src_node, src_idx, tgt_node, tgt_key):
    """Create a node link, silently suppressing type-mismatch exceptions."""
    try:
        src_out = src_node.outputs[src_idx]
        if isinstance(tgt_key, int):
            if tgt_key < len(tgt_node.inputs):
                links.new(src_out, tgt_node.inputs[tgt_key])
        elif tgt_key in tgt_node.inputs:
            links.new(src_out, tgt_node.inputs[tgt_key])
    except Exception:
        pass
