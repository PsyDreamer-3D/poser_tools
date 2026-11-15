# SPDX-License-Identifier: GPL-3.0-or-later
"""
injection_package.py
---------------------
Loads a Poser morph-injection package (a .pz2 file) into one combined Figure,
transparently following orchestrator readScript references.

Some real packages are a single file with inline targetGeom/deltas -- e.g.
InjDeltas.DC_39_BrowHeavy.pz2 -- and CR2Parser already handles those with no
new code (docs/handoff-morph-injection.md, Phase 5's original spike). Others
(e.g. "! All Morphs.pz2") are orchestrators: a flat list of
`readScript ":Runtime:...:file.pz2"` directives, each pointing at one real
per-morph file to actually parse.

cr2_importer never needed this -- its pz2_parser.py only *detects* the
'orchestrator' category (pz2_sniff), it never follows readScript -- so this
is original code, not an adaptation, unlike core/cr2/poser_paths.py which it
composes with.
"""

import os

from .cr2_parser import CR2Parser, CR2Tokenizer, Figure
from .poser_io import read_poser_file
from .poser_paths import resolve_poser_path


def load_injection_package(path: str, extra_roots=None) -> dict:
    """Load one morph-package file, resolving it fully into a single Figure.

    A plain inline-delta file is parsed and returned as-is. An orchestrator
    (one containing `readScript` directives) has each reference resolved
    (core/cr2/poser_paths.py) and recursively parsed, with every referenced
    file's actors folded into one combined Figure -- so
    core/cr2/name_match.py's indexing works the same way regardless of how
    many underlying files actually contributed to the package. Recursion
    handles an orchestrator referencing another orchestrator; a `seen` guard
    prevents re-parsing (or infinitely looping on) a file more than once.

    A visibility-only file (Poser's "Unhide.*" convention -- just a `hidden 0`
    toggle, no deltas) parses fine and folds in like any other leaf file; it's
    naturally inert downstream since its channel carries no deltas.

    `extra_roots` are the user's other Poser content roots (each the folder
    that contains a Runtime folder), searched after the referencing file's own
    Runtime. A package routinely lives in one library and names files in
    another (a product's pose entry in one folder, its figure's deltas under
    "Base Figures"), so without them those references go unresolved.

    Returns:
        {
            "figure": Figure,           -- combined actors from every file
            "unresolved_paths": [str],  -- readScript targets that couldn't be
                                           resolved to a real file on disk;
                                           reported, not silently dropped
            "leaf_paths": [str],        -- every file that was actually parsed
                                           (the file itself for a plain package)
        }
    """
    figure = Figure(source_file=path)
    unresolved_paths = []
    leaf_paths = []
    # One cache per load: a big orchestrator names hundreds of files in a
    # handful of folders, and re-listing them per target dominates the cost.
    _load_into(path, figure, unresolved_paths, leaf_paths, seen=set(), dir_cache={},
               extra_roots=extra_roots)
    return {"figure": figure, "unresolved_paths": unresolved_paths, "leaf_paths": leaf_paths}


def paired_injection_path(removal_path: str):
    """The `InjDeltas.X` file next to a `RemDeltas.X` one, or None.

    Poser ships the two as a pair, matched by file name. Remove normally works
    from the channel names inside the picked file, but two real Rem files
    (docs/handoff-morph-injection.md, Phase 6) name a different channel than
    their Inj file, so the filename pairing is the only link left. Case is
    ignored, like every other path lookup here.
    """
    folder, name = os.path.split(removal_path)
    prefix = "remdeltas."
    if not name.lower().startswith(prefix):
        return None
    wanted = ("injdeltas." + name[len(prefix):]).lower()
    try:
        siblings = os.listdir(folder or ".")
    except OSError:
        return None
    for sibling in siblings:
        if sibling.lower() == wanted:
            return os.path.join(folder, sibling)
    return None


def _load_into(path: str, combined: Figure, unresolved_paths: list, leaf_paths: list,
               seen: set, dir_cache: dict, extra_roots=None) -> None:
    real_path = os.path.realpath(path)
    if real_path in seen:
        return
    seen.add(real_path)

    text = read_poser_file(path)
    tokens = CR2Tokenizer.tokenize(text)

    if "readScript" not in tokens:
        # Leaf file: parse directly and fold its actors into the combined
        # figure. Deliberately not deduplicating actor names across files here
        # -- Phase 3 only ever iterates combined.actors (via name_match.py),
        # never combined.actor_map, so a same-named actor declared in two
        # different leaf files just means actor_map keeps whichever was
        # processed last; both actor objects still exist in .actors.
        leaf_paths.append(path)
        sub_figure = CR2Parser.parse_text(text, source_file=path)
        combined.actors.extend(sub_figure.actors)
        for actor in sub_figure.actors:
            combined.actor_map[actor.name] = actor
        return

    for i, tok in enumerate(tokens):
        if tok != "readScript" or i + 1 >= len(tokens):
            continue
        colon_path = tokens[i + 1]
        resolved = resolve_poser_path(
            colon_path, path, extra_roots=extra_roots, dir_cache=dir_cache
        )
        if resolved is None:
            unresolved_paths.append(colon_path)
            continue
        _load_into(resolved, combined, unresolved_paths, leaf_paths, seen, dir_cache, extra_roots)
