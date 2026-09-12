# SPDX-License-Identifier: MIT
# Adapted from PsyDreamer-3D/cr2_importer (unmaintained); see
# docs/handoff-shapekey-improvements.md Phase 5 for why this moved here and
# what changed (the internal_name space-token fix in _parse_channel()).
"""
cr2_parser.py
-------------
A standalone Python parser for Poser CR2 (Character Resource 2) files.
Validated against DAZ/Poser figures including Aiko 3 LE.

CR2 Structure notes (from real-file analysis):
  - Actors are declared TWICE: first as geometry stubs, then fully with channels.
    The parser merges these by name.
  - Channels live inside a  channels { ... }  wrapper block inside each actor.
  - Channel format:  channelType internalName { name ... initValue ... }
    e.g.  rotateY yrot { name yrot ... }
  - GetStringRes(1024,1) is a localization call used instead of string literals.
  - valueOpDeltaAdd is split across multiple lines:
        valueOpDeltaAdd
            Figure 1
            BODY:1
            PBMNavelGone
        deltaAddDelta 1.0
  - Morph deltas use:  d index dx dy dz
  - HR2/PP2 files use objFileGeom 0 0 :Runtime:... instead of figureResFile
  - Newer HR2/PP2 files use geomCustom { v ... vt ... f ... } for inline geometry
  - The 'name' keyword takes the rest of the line as its value (may contain spaces)
  - material blocks can appear inside prop actor blocks (HR2) as well as at root level

Usage:
    from cr2_parser import CR2Parser
    figure = CR2Parser.parse_file("MyFigure.cr2")
    for actor in figure.actors:
        print(actor.name, actor.parent)
"""

import os
import re
from dataclasses import dataclass, field
from typing import Optional

from .constants import TRANSFORM_CHANNELS


# ---------------------------------------------------------------------------
# Channel type keywords recognised in the channels { } block
# ---------------------------------------------------------------------------

# TRANSFORM_CHANNELS covers rotate/translate/scale variants; the extras here
# are CR2-specific dispatch tokens that also appear as channel-type keywords.
_CHANNEL_EXTRAS: frozenset = frozenset({
    'targetGeom', 'valueParm', 'deform',
    'sphereZone', 'capsuleZone',
    # Joint deformation channels — contain sphere weight data
    'jointX', 'jointY', 'jointZ',
    'twistX', 'twistY', 'twistZ',
})

CHANNEL_TYPES: frozenset = TRANSFORM_CHANNELS | _CHANNEL_EXTRAS


# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------

@dataclass
class ValueOperation:
    """A single ERC value operation linking one channel to another."""
    op_type: str
    target_figure: str = ""
    target_actor: str = ""
    target_channel: str = ""
    delta: float = 0.0
    k: list = field(default_factory=list)


@dataclass
class Channel:
    """A parameter channel on an actor."""
    internal_name: str
    kind: str = ""
    display_name: str = ""
    default_val: float = 0.0
    keyed_val: Optional[float] = None   # first k-frame value from keys block; None if absent
    value_operations: list = field(default_factory=list)
    deltas: list = field(default_factory=list)


@dataclass
class JointChannel:
    """
    A joint deformation channel (jointX/Y/Z or twistX/Y/Z).
    Contains the spherical zone data Poser uses to compute vertex weights
    at the boundary between two adjacent actors.
    """
    internal_name: str
    kind: str = ""
    other_actor: str = ""
    center: tuple = (0.0, 0.0, 0.0)
    start_pt: float = 0.0
    end_pt: float = 0.0
    mat_inner: list = field(default_factory=list)
    mat_outer: list = field(default_factory=list)
    angles: tuple = ()
    flipped: bool = False
    calc_weights: bool = False
    weight_map_ids: list = field(default_factory=list)


@dataclass
class Actor:
    """A single body part / node in the figure hierarchy."""
    name: str
    display_name: str = ""
    parent: Optional[str] = None
    channels: list = field(default_factory=list)
    joint_channels: list = field(default_factory=list)
    geom_name: str = ""
    geom_file: str = ""
    geom_custom: str = ""  # inline OBJ text from geomCustom block (no external file)
    origin: tuple = (0.0, 0.0, 0.0)
    endpoint: tuple = (0.0, 0.0, 0.0)
    smart_parent: str = ""  # Poser bone name from 'smartparent' token (:N suffix stripped)


@dataclass
class PoserShaderInput:
    """One input socket on a Poser shader node."""
    name: str
    value: Optional[tuple] = None    # (v1, v2, v3) — color RGB or scalar (current, min, max)
    file: Optional[str] = None       # Poser colon-path to an image texture
    node_ref: Optional[str] = None   # instance name of the upstream node feeding this input


@dataclass
class PoserShaderNode:
    """One node in a Poser shader tree."""
    node_type: str                              # category: 'poser', 'image_map', 'blender', …
    node_name: str                              # unique instance name within the tree
    inputs: dict = field(default_factory=dict)  # {input_key: PoserShaderInput}


@dataclass
class PoserShaderTree:
    """Full shader node graph parsed from a shaderTree { } block."""
    nodes: list = field(default_factory=list)  # [PoserShaderNode] in declaration order
    firefly_root: str = ''                     # instance name of the Firefly renderer root
    superfly_root: str = ''                    # instance name of the Superfly/Cycles root


@dataclass
class PoserMaterial:
    """Surface material parsed from a CR2 material block."""
    name: str
    diffuse_color:  tuple = (0.8, 0.8, 0.8)
    specular_color: tuple = (0.0, 0.0, 0.0)
    roughness:     float = 0.5
    bump_strength: float = 1.0
    transparency:  float = 0.0
    diffuse_tex:     Optional[str] = None
    bump_tex:        Optional[str] = None
    transparency_tex: Optional[str] = None
    specular_tex:    Optional[str] = None
    shader_tree: Optional[PoserShaderTree] = None


@dataclass
class Figure:
    """Top-level figure parsed from a CR2/HR2/PP2 file."""
    name: str = ""
    source_file: str = ""
    geom_file: str = ""
    geom_custom: str = ""  # inline OBJ text from geomCustom block (no external file needed)
    smart_parent: str = ""  # bubbled up from first actor that declares one (PP2/HR2 only)
    actors: list = field(default_factory=list)
    actor_map: dict = field(default_factory=dict)
    raw_weight_maps: dict = field(default_factory=dict)
    materials: dict = field(default_factory=dict)

    @property
    def is_weight_mapped(self) -> bool:
        return bool(self.raw_weight_maps)

    def get_actor(self, name: str) -> Optional[Actor]:
        return self.actor_map.get(name)

    def hierarchy(self) -> dict:
        tree = {a.name: [] for a in self.actors}
        for a in self.actors:
            if a.parent and a.parent in tree:
                tree[a.parent].append(a.name)
        return tree

    def print_hierarchy(self, root: str = None, indent: int = 0):
        tree = self.hierarchy()
        if root is None:
            roots = [a.name for a in self.actors
                     if not a.parent or a.parent not in self.actor_map]
            for r in roots:
                self.print_hierarchy(r, 0)
            return
        print("  " * indent + root)
        for child in tree.get(root, []):
            self.print_hierarchy(child, indent + 1)


# ---------------------------------------------------------------------------
# Tokenizer
# ---------------------------------------------------------------------------

class CR2Tokenizer:
    """Tokenizes CR2/HR2/PP2 files. GetStringRes(n,m) matched as single token."""

    TOKEN_RE = re.compile(
        r'GetStringRes\(\d+,\d+\)'
        r'|"[^"]*"'
        r'|[{}]'
        r'|[^\s{}"#]+'
    )

    @classmethod
    def tokenize(cls, text: str) -> list:
        tokens = []
        for line in text.splitlines():
            comment_idx = line.find('#')
            if comment_idx >= 0:
                line = line[:comment_idx]
            line = line.strip()
            if not line:
                continue

            # Special case: the 'name' keyword takes the REST of the line as
            # its value.  Poser display names can contain spaces (e.g.
            # "name    A3 Mitsu Hair").  Emit 'name' + the full remainder as
            # two tokens so multi-word names are not split by the regex.
            if len(line) > 4 and line[:4] == 'name' and line[4] in (' ', '\t'):
                rest = line[4:].strip()
                if rest:
                    if rest.startswith('"') and rest.endswith('"'):
                        rest = rest[1:-1]
                    tokens.append('name')
                    tokens.append(rest)
                continue

            for m in cls.TOKEN_RE.finditer(line):
                tok = m.group(0)
                if tok.startswith('"') and tok.endswith('"'):
                    tok = tok[1:-1]
                tokens.append(tok)
        return tokens


# ---------------------------------------------------------------------------
# Parser
# ---------------------------------------------------------------------------

class CR2Parser:

    def __init__(self, tokens: list):
        self.tokens = tokens
        self.pos = 0

    def peek(self) -> Optional[str]:
        return self.tokens[self.pos] if self.pos < len(self.tokens) else None

    def consume(self) -> str:
        tok = self.tokens[self.pos]
        self.pos += 1
        return tok

    def expect(self, value: str):
        tok = self.consume()
        if tok != value:
            ctx = self.tokens[max(0, self.pos-3):self.pos+3]
            raise ValueError(f"Expected '{value}', got '{tok}'. Context: {ctx}")

    def maybe(self, value: str) -> bool:
        if self.peek() == value:
            self.consume()
            return True
        return False

    def consume_float(self) -> float:
        tok = self.consume()
        try:
            return float(tok)
        except (ValueError, TypeError):
            tok_upper = (tok or '').upper()
            if 'QNAN' in tok_upper or 'IND' in tok_upper:
                return 0.0
            if 'INF' in tok_upper:
                return float('inf') if not tok_upper.startswith('-') else float('-inf')
            return 0.0

    def consume_int(self) -> int:
        return int(self.consume())

    def skip_block(self):
        depth = 0
        while self.pos < len(self.tokens):
            tok = self.consume()
            if tok == '{':
                depth += 1
            elif tok == '}':
                depth -= 1
                if depth == 0:
                    return

    def collect_block_raw(self) -> list:
        raw = []
        self.expect('{')
        depth = 1
        while self.pos < len(self.tokens) and depth > 0:
            tok = self.consume()
            if tok == '{':
                depth += 1
            elif tok == '}':
                depth -= 1
                if depth == 0:
                    break
            raw.append(tok)
        return raw

    def is_getstring(self, tok: str) -> bool:
        return tok is not None and tok.startswith('GetStringRes(')

    # ------------------------------------------------------------------
    # Entry points
    # ------------------------------------------------------------------

    @classmethod
    def parse_file(cls, filepath: str) -> Figure:
        from .poser_io import read_poser_file
        text = read_poser_file(filepath)
        return cls.parse_text(text, source_file=filepath)

    @classmethod
    def parse_text(cls, text: str, source_file: str = "") -> Figure:
        tokens = CR2Tokenizer.tokenize(text)
        parser = cls(tokens)
        figure = parser._parse_root()
        figure.source_file = source_file
        return figure

    def _parse_root(self) -> Figure:
        figure = Figure()
        self.maybe('{')

        while self.pos < len(self.tokens):
            tok = self.peek()
            if tok is None:
                break
            elif tok == 'version':
                self.consume()
                if self.peek() == '{':
                    self.skip_block()
            elif tok == 'figureResFile':
                self.consume()
                figure.geom_file = self.consume()
                figure.name = figure.geom_file.split(':')[-1]
            elif tok == 'controlProp':
                self.consume()
                self.consume()  # name
                if self.peek() == '{':
                    self.skip_block()
            elif tok in ('actor', 'prop'):
                kind = self.consume()
                self._parse_actor_into(figure, kind)
            elif tok == 'material':
                mat = self._parse_material_block()
                if mat is not None:
                    figure.materials[mat.name] = mat
            elif tok == '}':
                self.consume()
            else:
                self.consume()

        return figure

    # ------------------------------------------------------------------
    # Material block — appears at root level or inside prop actors (HR2)
    # ------------------------------------------------------------------

    def _parse_material_block(self) -> Optional[PoserMaterial]:
        self.consume()  # 'material'
        name = self.consume()

        if self.peek() != '{':
            return None

        self.expect('{')

        kd = (0.8, 0.8, 0.8); ks = (0.0, 0.0, 0.0)
        ns_exponent = 50.0; bump_str = 1.0
        t_min = 0.0; t_max = 0.0
        tex_map = bump_map = trans_map = spec_tex = None
        shader_tree = None

        while self.peek() not in ('}', None):
            tok = self.peek()
            if tok == 'KdColor':
                self.consume()
                r, g, b = self.consume_float(), self.consume_float(), self.consume_float()
                self.consume_float(); kd = (r, g, b)
            elif tok == 'KsColor':
                self.consume()
                r, g, b = self.consume_float(), self.consume_float(), self.consume_float()
                self.consume_float(); ks = (r, g, b)
            elif tok == 'NsExponent':
                self.consume(); ns_exponent = self.consume_float()
            elif tok == 'bumpStrength':
                self.consume(); bump_str = self.consume_float()
            elif tok == 'tMin':
                self.consume(); t_min = self.consume_float()
            elif tok == 'tMax':
                self.consume(); t_max = self.consume_float()
            elif tok == 'textureMap':
                self.consume()
                path = self.consume()
                if path not in ('NO_MAP', '"NO_MAP"'):
                    tex_map = path.strip('"')
                    for _ in range(2):
                        if self.peek() not in (None, '}') and self._is_float(self.peek()):
                            self.consume()
            elif tok == 'bumpMap':
                self.consume(); path = self.consume()
                if path not in ('NO_MAP', '"NO_MAP"'): bump_map = path.strip('"')
            elif tok == 'transparencyMap':
                self.consume(); path = self.consume()
                if path not in ('NO_MAP', '"NO_MAP"'): trans_map = path.strip('"')
            elif tok == 'shaderTree':
                self.consume()
                if self.peek() == '{':
                    shader_tree = self._parse_shader_tree()
            elif tok == '{':
                self.skip_block()
            else:
                self.consume()

        self.maybe('}')

        # Derive backward-compat texture fields from the shader tree when not
        # already set by top-level textureMap / bumpMap / transparencyMap keywords.
        if shader_tree is not None:
            node_files = self._shader_tree_fallback_files(shader_tree)
            if tex_map is None:
                tex_map = node_files.get('Diffuse_Color') or node_files.get('diffuse')
            if bump_map is None:
                bump_map = node_files.get('Bump') or node_files.get('bump')
            if trans_map is None:
                trans_map = (node_files.get('Transparency_Max')
                             or node_files.get('Transparency'))
            if spec_tex is None:
                spec_tex = (node_files.get('Highlight_Color')
                            or node_files.get('Specular_Color'))

        import math as _math
        roughness = min(1.0, max(0.05, _math.sqrt(2.0 / (ns_exponent + 2.0))))
        transparency = t_min if t_max < 1.0 else 1.0

        return PoserMaterial(
            name=name, diffuse_color=kd, specular_color=ks,
            roughness=roughness, bump_strength=bump_str, transparency=transparency,
            diffuse_tex=tex_map, bump_tex=bump_map,
            transparency_tex=trans_map, specular_tex=spec_tex,
            shader_tree=shader_tree,
        )

    def _parse_shader_tree(self) -> PoserShaderTree:
        """
        Parse a shaderTree { } block into a PoserShaderTree.

        Token format (quotes are stripped by CR2Tokenizer before this runs):
          node <category> <instance_name> {
              name <display_name>
              pos X Y
              nodeInput <key> {
                  name <display_name>
                  value V1 V2 V3
                  parmR NO_PARM
                  parmG NO_PARM
                  parmB NO_PARM
                  node NO_NODE | <source_instance_name>
                  file NO_MAP  | <colon_path>
              }
              ...
          }
          ...
          fireflyRoot <instance_name>
          superflyRoot <instance_name>
        """
        self.expect('{')
        depth = 1
        tree = PoserShaderTree()
        current_node: Optional[PoserShaderNode] = None
        current_input_name: Optional[str] = None

        while self.pos < len(self.tokens) and depth > 0:
            tok = self.peek()
            if tok is None:
                break

            if tok == '{':
                depth += 1
                self.consume()

            elif tok == '}':
                depth -= 1
                self.consume()
                if depth == 2:
                    current_input_name = None           # leaving nodeInput body
                elif depth == 1:
                    if current_node is not None:        # leaving node body
                        tree.nodes.append(current_node)
                    current_node = None

            elif tok == 'node' and depth == 1:
                # node <category> <instance_name>
                self.consume()
                node_type = self.consume()
                node_name = self.consume()
                current_node = PoserShaderNode(node_type=node_type, node_name=node_name)

            elif tok == 'nodeInput' and depth == 2 and current_node is not None:
                # nodeInput <key>
                self.consume()
                key = self.consume()
                current_input_name = key
                current_node.inputs[key] = PoserShaderInput(name=key)

            elif depth == 3 and current_node is not None and current_input_name is not None:
                inp = current_node.inputs[current_input_name]
                if tok == 'value':
                    self.consume()
                    inp.value = (self.consume_float(),
                                 self.consume_float(),
                                 self.consume_float())
                elif tok == 'file':
                    self.consume()
                    path = self.consume()
                    if path not in ('NO_MAP', '"NO_MAP"'):
                        inp.file = path
                elif tok == 'node':
                    self.consume()
                    ref = self.consume()
                    if ref != 'NO_NODE':
                        inp.node_ref = ref
                else:
                    self.consume()  # parmR, parmG, parmB, name, …

            elif tok == 'fireflyRoot' and depth == 1:
                self.consume()
                tree.firefly_root = self.consume()

            elif tok == 'superflyRoot' and depth == 1:
                self.consume()
                tree.superfly_root = self.consume()

            else:
                self.consume()  # pos, name, advancedInputsCollapsed, gamma, …

        return tree

    @staticmethod
    def _shader_tree_fallback_files(tree: PoserShaderTree) -> dict:
        """
        Extract a flat {input_key: file_path} mapping from a PoserShaderTree
        for use as backward-compatible texture fallbacks in PoserMaterial fields.
        Collects every non-empty file path keyed by the nodeInput name in which
        it appears; last occurrence wins when a key is repeated across nodes.
        """
        result = {}
        for node in tree.nodes:
            for key, inp in node.inputs.items():
                if inp.file:
                    result[key] = inp.file
        return result

    @staticmethod
    def _is_float(tok: str) -> bool:
        try:
            float(tok); return True
        except (ValueError, TypeError):
            return False

    # ------------------------------------------------------------------
    # Actor — merges stub + full declaration
    # ------------------------------------------------------------------

    def _parse_actor_into(self, figure: Figure, kind: str = 'actor'):
        raw_name = self.consume()
        name = raw_name.split(':')[0]

        if name in figure.actor_map:
            actor = figure.actor_map[name]
        else:
            actor = Actor(name=name)
            figure.actors.append(actor)
            figure.actor_map[name] = actor

        if self.peek() != '{':
            return

        self.expect('{')
        while self.peek() not in ('}', None):
            tok = self.peek()

            if tok == 'name':
                self.consume()
                val = self.consume()
                if not self.is_getstring(val):
                    actor.display_name = val

            elif tok == 'parent':
                self.consume()
                p = self.consume().split(':')[0]
                actor.parent = None if p == 'UNIVERSE' else p

            elif tok == 'smartparent':
                self.consume()
                raw = self.consume()           # e.g. "rHand:1" or "head"
                val = raw.split(':')[0]
                actor.smart_parent = "" if val == 'UNIVERSE' else val

            elif tok == 'origin':
                self.consume()
                actor.origin = (self.consume_float(), self.consume_float(), self.consume_float())

            elif tok == 'endPoint':
                self.consume()
                actor.endpoint = (self.consume_float(), self.consume_float(), self.consume_float())

            elif tok == 'orientation':
                self.consume()
                self.consume_float(); self.consume_float(); self.consume_float()

            elif tok == 'storageOffset':
                self.consume()
                self.consume_float(); self.consume_float(); self.consume_float()

            elif tok == 'geomHandlerGeom':
                self.consume()
                self.consume_int()   # handler index (unused)
                actor.geom_name = self.consume()

            elif tok == 'channels':
                self.consume()
                self._parse_channels_block(actor)

            elif tok == 'weightMap':
                self._parse_weight_map_block(figure)

            elif tok == 'objFileGeom':
                # HR2/PP2 format: objFileGeom <int> <int> <colon-path>
                # e.g. objFileGeom 0 0 :Runtime:Geometries:DAZHair:3duMitsuHair.obj
                self.consume()
                self.consume_int()   # handler index (always 0)
                self.consume_int()   # sub-index (always 0)
                actor.geom_file = self.consume()

            elif tok == 'geomCustom':
                # Newer HR2/PP2 format: geometry is embedded inline rather than
                # referencing a separate OBJ file.
                self.consume()
                actor.geom_custom = self._parse_geom_custom_block()

            elif tok == 'geometry':
                self.consume()
                if self.peek() == '{':
                    raw = self.collect_block_raw()
                    for i, t in enumerate(raw):
                        if t == 'objFile' and i + 1 < len(raw):
                            actor.geom_file = raw[i + 1]
                else:
                    actor.geom_file = self.consume()

            elif tok == 'material':
                # Material blocks can appear inside a prop actor (e.g. HR2 files
                # store them under the second prop figureHair declaration) as well
                # as at the root level.  Parse and store on the figure either way.
                mat = self._parse_material_block()
                if mat is not None:
                    figure.materials[mat.name] = mat

            elif tok in CHANNEL_TYPES:
                # Some CR2 files place channel blocks directly inside the actor
                # body rather than nesting them under a 'channels { }' block.
                # Parse them the same way _parse_channels_block would.
                actor.channels.append(self._parse_channel())

            elif tok == '{':
                self.skip_block()

            else:
                self.consume()

        self.maybe('}')

    # ------------------------------------------------------------------
    # geomCustom { numbVerts N ... v x y z ... vt u v ... f ... g ... }
    # ------------------------------------------------------------------

    def _parse_geom_custom_block(self) -> str:
        """
        Parse an inline geomCustom block and return OBJ-compatible text.

        geomCustom blocks embed geometry directly in the HR2/PP2 file using
        standard OBJ directives (v, vt, f, g, usemtl, s) plus count headers
        (numbVerts, numbTVerts, numbTSets, numbElems, numbSets) that are
        not used by OBJLoader and are silently skipped.

        The reconstructed text is returned as a string that OBJLoader._parse()
        can consume directly — no external file needed.
        """
        if self.peek() != '{':
            return ''
        self.expect('{')

        SKIP_KEYWORDS = {'numbVerts', 'numbTVerts', 'numbTSets', 'numbElems', 'numbSets'}

        lines = []
        depth = 1

        while self.pos < len(self.tokens) and depth > 0:
            tok = self.peek()
            if tok is None:
                break
            if tok == '{':
                depth += 1
                self.consume()
                continue
            if tok == '}':
                depth -= 1
                self.consume()
                continue

            if tok in SKIP_KEYWORDS:
                self.consume()
                if self.peek() not in ('{', '}', None):
                    self.consume()
                continue

            if tok == 'v':
                self.consume()
                x = self.consume(); y = self.consume(); z = self.consume()
                lines.append(f'v {x} {y} {z}')

            elif tok == 'vt':
                self.consume()
                u = self.consume(); v = self.consume()
                lines.append(f'vt {u} {v}')

            elif tok == 'vn':
                self.consume()
                nx = self.consume(); ny = self.consume(); nz = self.consume()
                lines.append(f'vn {nx} {ny} {nz}')

            elif tok == 'f':
                self.consume()
                NON_CORNER = {'v', 'vt', 'vn', 'f', 'g', 'usemtl', 's',
                              '}', '{', None} | SKIP_KEYWORDS
                corners = []
                while self.peek() not in NON_CORNER:
                    corners.append(self.consume())
                if corners:
                    lines.append('f ' + ' '.join(corners))

            elif tok == 'g':
                self.consume()
                NON_NAME = {'v', 'vt', 'vn', 'f', 'g', 'usemtl', 's',
                            '}', '{', None} | SKIP_KEYWORDS
                parts = []
                while self.peek() not in NON_NAME:
                    parts.append(self.consume())
                name = ' '.join(parts) if parts else 'default'
                lines.append(f'g {name}')

            elif tok == 'usemtl':
                self.consume()
                mat = self.consume() if self.peek() not in ('}', '{', None) else ''
                if mat:
                    lines.append(f'usemtl {mat}')

            elif tok == 's':
                self.consume()
                val = self.consume() if self.peek() not in ('}', '{', None) else ''
                lines.append(f's {val}')

            else:
                self.consume()

        return '\n'.join(lines)

    # ------------------------------------------------------------------
    # weightMap <ID> { numbVerts N; v localIdx weight; ... }
    # ------------------------------------------------------------------

    def _parse_weight_map_block(self, figure: Figure):
        self.consume()           # 'weightMap'
        map_id = self.consume()  # numeric ID string

        if self.peek() != '{':
            return

        self.expect('{')
        entries = {}
        while self.peek() not in ('}', None):
            tok = self.peek()
            if tok == 'numbVerts':
                self.consume(); self.consume()
            elif tok == 'v':
                self.consume()
                try:
                    local_idx = self.consume_int()
                    weight    = self.consume_float()
                    entries[local_idx] = weight
                except (ValueError, IndexError):
                    pass
            elif tok == '{':
                self.skip_block()
            else:
                self.consume()
        self.maybe('}')

        if entries and map_id not in figure.raw_weight_maps:
            figure.raw_weight_maps[map_id] = entries

    def _parse_channels_block(self, actor: Actor):
        if self.peek() != '{':
            return
        self.expect('{')

        while self.peek() not in ('}', None):
            tok = self.peek()
            if tok in ('jointX', 'jointY', 'jointZ',
                       'twistX', 'twistY', 'twistZ'):
                actor.joint_channels.append(self._parse_joint_channel())
            elif tok in CHANNEL_TYPES:
                actor.channels.append(self._parse_channel())
            elif tok in ('sphereZone', 'capsuleZone',
                         'sphericalFalloffZone', 'capsuleFalloffZone', 'jointZone'):
                self.consume()
                if self.peek() == '{': self.skip_block()
            elif tok == '{':
                self.skip_block()
            else:
                self.consume()

        self.maybe('}')

    def _parse_channel(self) -> Channel:
        kind = self.consume()
        if self.peek() == '{':
            # Some channels (e.g. bare 'scale') have no internal name token
            # and open their block immediately.
            internal_name = ''
        else:
            # internal_name is a bare, unquoted token run: Poser allows it to
            # contain spaces (e.g. "targetGeom Blink Right", "targetGeom Toes
            # Grasp"), the same grammar CR2Tokenizer.tokenize() already special-
            # cases for the 'name' keyword. A single consume() here truncates
            # the name at the first space and leaves the rest of it (plus the
            # channel's own '{') to be silently swallowed by the caller's
            # fallback token handling, losing the whole channel body.
            parts = []
            while self.peek() is not None and self.peek() != '{':
                parts.append(self.consume())
            internal_name = ' '.join(parts)
        ch = Channel(internal_name=internal_name, kind=kind)

        if self.peek() != '{':
            return ch

        self.expect('{')

        while self.peek() not in ('}', None):
            tok = self.peek()

            if tok == 'name':
                self.consume(); val = self.consume()
                if not self.is_getstring(val): ch.display_name = val
            elif tok == 'initValue': self.consume(); ch.default_val = self.consume_float()
            elif tok in ('value', 'hidden', 'forceLimits', 'min', 'max',
                         'trackable', 'static'): self.consume(); self.consume()
            elif tok == 'keys':
                self.consume()
                if self.peek() == '{':
                    self.expect('{')
                    while self.peek() not in ('}', None):
                        if self.peek() == 'k':
                            self.consume()              # 'k'
                            self.consume()              # frame number (discarded)
                            val = self.consume_float()  # position/value
                            if ch.keyed_val is None:    # first key only
                                ch.keyed_val = val
                        else:
                            self.consume()              # skip 'static', interpolation flags, etc.
                    self.maybe('}')
            elif tok == 'trackingScale':
                self.consume()
                if self.peek() == '{': self.skip_block()
            elif tok == 'interpStyleLocked':
                self.consume(); self.consume()
            elif tok == 'valueOpDeltaAdd':
                ch.value_operations.append(self._parse_valueop_delta_add())
            elif tok in ('indexes', 'numbDeltas'): self.consume(); self.consume()
            elif tok == 'deltas':
                self.consume()
                if self.peek() == '{': ch.deltas = self._parse_morph_deltas()
            elif tok in ('enabled', 'masterSynched', 'trackingScaleMult'): self.consume(); self.consume()
            elif tok == 'staticValue': self.consume(); self.consume_float()
            elif tok == 'blendType': self.consume(); self.consume()
            elif tok in ('targetExtendedInfo', 'groupNode'):
                self.consume()
                if self.peek() == '{': self.skip_block()
            elif tok == '{': self.skip_block()
            else: self.consume()

        self.maybe('}')
        return ch

    def _parse_valueop_delta_add(self) -> ValueOperation:
        self.consume()  # 'valueOpDeltaAdd'
        vo = ValueOperation(op_type='deltaAdd')
        STOP = {'valueOpDeltaAdd', 'valueOpScaleAdd', 'valueOpKey', 'valueOpPlus',
                'name', 'initValue', 'hidden', 'forceLimits', 'min', 'max',
                'keys', 'interpStyleLocked', 'trackingScale', 'indexes',
                'numbDeltas', 'deltas', 'enabled', 'masterSynched',
                'staticValue', 'blendType', 'targetExtendedInfo',
                'groupNode', '}', '{', None}
        if self.peek() == 'Figure':
            self.consume(); vo.target_figure = self.consume()
        if self.peek() not in STOP:
            vo.target_actor = self.consume().split(':')[0]
        if self.peek() not in STOP:
            vo.target_channel = self.consume()
        while self.peek() not in STOP:
            tok = self.peek()
            if tok == 'strength': self.consume(); self.consume_float()
            elif tok == 'deltaAddDelta': self.consume(); vo.delta = self.consume_float()
            else: break
        return vo

    def _parse_morph_deltas(self) -> list:
        deltas = []
        self.expect('{')
        while self.peek() not in ('}', None):
            tok = self.peek()
            if tok == 'd':
                self.consume()
                try:
                    idx = self.consume_int()
                    dx = self.consume_float(); dy = self.consume_float(); dz = self.consume_float()
                    deltas.append((idx, dx, dy, dz))
                except (ValueError, IndexError): break
            else: self.consume()
        self.maybe('}')
        return deltas

    def _parse_joint_channel(self) -> JointChannel:
        kind = self.consume()
        internal_name = self.consume()
        jc = JointChannel(internal_name=internal_name, kind=kind)
        if self.peek() != '{': return jc
        self.expect('{')
        while self.peek() not in ('}', None):
            tok = self.peek()
            if tok == 'name': self.consume(); self.consume()
            elif tok == 'otherActor': self.consume(); jc.other_actor = self.consume().split(':')[0]
            elif tok == 'matrixActor': self.consume(); self.consume()
            elif tok == 'center':
                self.consume()
                jc.center = (self.consume_float(), self.consume_float(), self.consume_float())
            elif tok == 'startPt': self.consume(); jc.start_pt = self.consume_float()
            elif tok == 'endPt': self.consume(); jc.end_pt = self.consume_float()
            elif tok == 'angles': self.consume(); jc.angles = tuple(self.consume_float() for _ in range(4))
            elif tok == 'flipped': self.consume(); jc.flipped = True
            elif tok == 'calcWeights': self.consume(); jc.calc_weights = True
            elif tok == 'zones':
                self.consume()
                if self.peek() == '{':
                    self.expect('{')
                    while self.peek() not in ('}', None):
                        ztok = self.peek()
                        if ztok in ('spherezone', 'weightmapzone', 'sphereZone', 'weightMapZone'):
                            self.consume()
                            if self.peek() == '{':
                                self.expect('{')
                                while self.peek() not in ('}', None):
                                    ztok2 = self.peek()
                                    if ztok2 == 'mapname': self.consume(); jc.weight_map_ids.append(self.consume())
                                    elif ztok2 == '{': self.skip_block()
                                    else: self.consume()
                                self.maybe('}')
                        elif ztok == '{': self.skip_block()
                        else: self.consume()
                    self.maybe('}')
            elif tok == 'sphereMatsRaw':
                self.consume()
                vals = []
                for _ in range(32):
                    try: vals.append(self.consume_float())
                    except (ValueError, IndexError): break
                if len(vals) == 32:
                    jc.mat_inner = vals[:16]; jc.mat_outer = vals[16:]
            elif tok in ('initValue', 'hidden', 'forceLimits', 'min', 'max',
                         'trackingScale', 'interpStyleLocked', 'static', 'enabled', 'masterSynched'):
                self.consume(); self.consume()
            elif tok == 'keys':
                self.consume()
                if self.peek() == '{': self.skip_block()
            elif tok in ('doBulge', 'posBulgeLeft', 'posBulgeRight', 'negBulgeLeft', 'negBulgeRight',
                         'jointMult', 'useBulgeMapLeft', 'useBulgeMapRight', 'smoothZones', 'primaryParm'):
                self.consume()
                if self.peek() not in ('{', '}', None) and not self.peek().startswith('Get'):
                    self.consume()
                if self.peek() == '{': self.skip_block()
            elif tok == '{': self.skip_block()
            else: self.consume()
        self.maybe('}')
        return jc


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _resolve_geom_file(figure: Figure, source_path: str,
                       extra_roots: list = None) -> None:
    """
    Resolve figure.geom_file from a Poser colon-path to an absolute
    filesystem path, modifying the Figure in-place.
    """
    if not figure.geom_file:
        return
    if os.path.isabs(figure.geom_file) and os.path.isfile(figure.geom_file):
        return
    try:
        from .obj_loader import PoserPathResolver
    except ImportError:
        try:
            from obj_loader import PoserPathResolver  # type: ignore
        except ImportError:
            return
    resolved = PoserPathResolver.resolve(figure.geom_file,
                                         reference_file=source_path,
                                         extra_roots=extra_roots or [])
    if resolved:
        figure.geom_file = resolved


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def summarize(figure: Figure):
    print(f"\n{'='*60}")
    print(f"Figure : {figure.name}")
    print(f"Source : {figure.source_file}")
    print(f"Geom   : {figure.geom_file or ('(inline geomCustom)' if figure.geom_custom else '(none)')}")
    print(f"Mats   : {list(figure.materials.keys())}")
    print(f"Actors : {len(figure.actors)}")
    print(f"{'='*60}")
    figure.print_hierarchy()
    for actor in figure.actors:
        morphs = [c for c in actor.channels if c.kind == 'targetGeom']
        ercs   = sum(len(c.value_operations) for c in actor.channels)
        geom   = actor.geom_name or actor.geom_file or ('(inline)' if actor.geom_custom else '(none)')
        print(f"  {actor.name:<25} parent={str(actor.parent):<20} "
              f"ch={len(actor.channels):>3}  morphs={len(morphs):>3}  "
              f"ercs={ercs:>3}  geom={geom}")


if __name__ == '__main__':
    import sys
    if len(sys.argv) < 2:
        print("Usage: python cr2_parser.py <path/to/file.cr2>")
        sys.exit(1)
    fig = CR2Parser.parse_file(sys.argv[1])
    summarize(fig)
