# SPDX-License-Identifier: MIT
"""
material_builder.py
-------------------
Builds a Blender material's node tree from a parsed PoserMaterial: Poser's shader tree
through shader_translator when the material has one, else a Principled BSDF from the
legacy `KdColor`/`textureMap`/`bumpMap`/`transparencyMap` fields.

Adapted from cr2_importer (PsyDreamer-3D's own, now unmaintained CR2 importer),
core/material_pipeline.py `MaterialMixin._build_material_nodes()`/`_load_image()`, for
Phase 8 MAT poses (docs/handoff-morph-injection.md). MIT like the rest of the adapted code.
Changes: plain functions instead of a mixin; textures resolve through poser_paths'
case-insensitive lookup across the configured Runtime roots (Phase 7.0); transparency uses
Blender 4.2+'s `surface_render_method` (`shadow_method` no longer exists); warnings go to a
`reporter(types, message)` callback instead of print().
"""

from .cr2.poser_paths import resolve_poser_path
from .shader_translator import build_shader_tree


def make_image_loader(source_file: str, extra_roots=None, reporter=None):
    """A `load_image(poser_colon_path)` for one MAT file: an image datablock, or None.

    Paths resolve against the MAT file's own Runtime, then the configured roots (a character's
    textures often live in another library). Images already loaded are reused, so 30 materials
    pointing at one body texture load it once.
    """
    def load_image(poser_path):
        import bpy
        path = resolve_poser_path(poser_path, source_file, extra_roots=extra_roots)
        if not path:
            if reporter:
                reporter({'WARNING'}, f"Texture not found: {poser_path}")
            return None
        try:
            return bpy.data.images.load(path, check_existing=True)
        except RuntimeError as exc:
            if reporter:
                reporter({'WARNING'}, f"Could not load texture {path}: {exc}")
            return None
    return load_image


def _show_transparency(bl_mat):
    # EEVEE's dithered mode draws alpha cut-outs (eyelashes, brows) without sorting problems;
    # it is the default, but a material built by another tool may have been set to blended.
    if hasattr(bl_mat, "surface_render_method"):
        bl_mat.surface_render_method = 'DITHERED'
    elif hasattr(bl_mat, "blend_method"):
        bl_mat.blend_method = 'HASHED'


def build_material_nodes(bl_mat, pmat, load_image, reporter=None) -> str:
    """Replace `bl_mat`'s node tree with one built from `pmat`; returns "shader tree" or "legacy"."""
    bl_mat.use_nodes = True
    nodes = bl_mat.node_tree.nodes
    links = bl_mat.node_tree.links
    nodes.clear()

    if pmat.shader_tree and pmat.shader_tree.nodes:
        build_shader_tree(bl_mat, pmat.shader_tree, load_image_fn=load_image, reporter=reporter)
        return "shader tree"

    out = nodes.new('ShaderNodeOutputMaterial')
    out.location = (600, 0)
    bsdf = nodes.new('ShaderNodeBsdfPrincipled')
    bsdf.location = (300, 0)
    links.new(bsdf.outputs['BSDF'], out.inputs['Surface'])
    bsdf.inputs['Roughness'].default_value = pmat.roughness

    spec_lum = (pmat.specular_color[0] * 0.2126 + pmat.specular_color[1] * 0.7152
                + pmat.specular_color[2] * 0.0722)
    bsdf.inputs['Specular IOR Level'].default_value = spec_lum
    if pmat.specular_tex:
        image = load_image(pmat.specular_tex)
        if image:
            node = nodes.new('ShaderNodeTexImage')
            node.image = image
            node.location = (-400, 400)
            links.new(node.outputs['Color'], bsdf.inputs['Specular IOR Level'])

    if pmat.transparency > 0.0 or pmat.transparency_tex:
        _show_transparency(bl_mat)
        image = load_image(pmat.transparency_tex) if pmat.transparency_tex else None
        if image:
            image.colorspace_settings.name = 'Non-Color'
            node = nodes.new('ShaderNodeTexImage')
            node.image = image
            node.location = (-400, -300)
            links.new(node.outputs['Color'], bsdf.inputs['Alpha'])
        else:
            bsdf.inputs['Alpha'].default_value = 1.0 - pmat.transparency

    image = load_image(pmat.diffuse_tex) if pmat.diffuse_tex else None
    if image:
        node = nodes.new('ShaderNodeTexImage')
        node.image = image
        node.location = (-200, 200)
        links.new(node.outputs['Color'], bsdf.inputs['Base Color'])
    else:
        bsdf.inputs['Base Color'].default_value = (*pmat.diffuse_color, 1.0)

    if pmat.bump_tex and pmat.bump_strength > 0.0:
        image = load_image(pmat.bump_tex)
        if image:
            image.colorspace_settings.name = 'Non-Color'
            node = nodes.new('ShaderNodeTexImage')
            node.image = image
            node.location = (-400, -100)
            bump = nodes.new('ShaderNodeBump')
            bump.inputs['Strength'].default_value = min(pmat.bump_strength, 1.0)
            bump.location = (-100, -100)
            links.new(node.outputs['Color'], bump.inputs['Height'])
            links.new(bump.outputs['Normal'], bsdf.inputs['Normal'])
    return "legacy"
