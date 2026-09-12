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


def load_injection_package(path: str) -> dict:
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

    Returns:
        {
            "figure": Figure,           -- combined actors from every file
            "unresolved_paths": [str],  -- readScript targets that couldn't be
                                           resolved to a real file on disk;
                                           reported, not silently dropped
        }
    """
    figure = Figure(source_file=path)
    unresolved_paths = []
    _load_into(path, figure, unresolved_paths, seen=set())
    return {"figure": figure, "unresolved_paths": unresolved_paths}


def _load_into(path: str, combined: Figure, unresolved_paths: list, seen: set) -> None:
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
        sub_figure = CR2Parser.parse_text(text, source_file=path)
        combined.actors.extend(sub_figure.actors)
        for actor in sub_figure.actors:
            combined.actor_map[actor.name] = actor
        return

    for i, tok in enumerate(tokens):
        if tok != "readScript" or i + 1 >= len(tokens):
            continue
        colon_path = tokens[i + 1]
        resolved = resolve_poser_path(colon_path, path)
        if resolved is None:
            unresolved_paths.append(colon_path)
            continue
        _load_into(resolved, combined, unresolved_paths, seen)
