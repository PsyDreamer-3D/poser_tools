# SPDX-License-Identifier: GPL-3.0-or-later
"""
injection_scan.py
-----------------
A cheap look inside a morph-injection package: which morphs it holds, what
they're called, and which files each one lives in -- without parsing a single
delta.

Why this exists: listing a package's morphs for the picker (docs/handoff-
morph-injection.md, 7.2) must not cost what applying it costs. A full parse of
the 259-morph Victoria 3 head package takes ~9s; this takes ~1s, because the
bulk of every file is delta lines the list never needs.

Two readers of one format means they can drift. The scan is therefore only ever
used to *describe* a package; Apply still parses with CR2Parser, so a scan miss
hides a row from the list, it can never apply wrong data. tests/
test_injection_scan.py asserts the two agree, on synthetic files and (when the
Poser content drive is mounted) on real packages.
"""

import re
from dataclasses import dataclass, field

from .injection_package import resolve_package_files
from .name_match import pick_display_name
from .poser_io import read_poser_file

_BRACE_RE = re.compile(r'[{}]')
_TARGET_GEOM_HEADER_RE = re.compile(r'^targetGeom[ \t]+(.+)$')
# CR2Parser reads channels under both `actor` and `prop` blocks (hair and other props carry them).
_CHANNEL_OWNER_RE = re.compile(r'^(?:actor|prop)[ \t]')
# CR2Parser keeps the rest of the line as a channel's label ("Blink Right",
# "EMPTY-OW (OAt)"), not just its first word; a pair of double quotes is dropped.
_NAME_RE = re.compile(r'^[ \t]*name[ \t]+(.+?)[ \t\r]*$', re.M)
_INDEXES_RE = re.compile(r'^[ \t]*indexes[ \t]+(\d+)', re.M)
_BINARY_RE = re.compile(r'^[ \t]*useBinaryMorph[ \t]+(\d+)', re.M)


@dataclass
class ScannedChannel:
    internal_name: str
    display_name: str       # "" when the channel never set one
    delta_count: int        # the `indexes` count == number of `d` lines CR2Parser keeps
    uses_binary_morph: bool


@dataclass
class ScannedMorph:
    """One internal_name across every file and actor that declares it."""
    internal_name: str
    display_name: str
    delta_count: int                 # summed over the morph's channels
    uses_binary_morph: bool
    leaf_paths: list = field(default_factory=list)   # files to parse to apply it, file order


def scan_channels(text: str) -> list:
    """Every targetGeom channel in one file's text, in file order.

    One pass over the braces, tracking which blocks enclose the current one, so
    that exactly the channels CR2Parser keeps are reported: a `targetGeom`
    directly inside `actor { channels { ... } }` (or `prop`). Pose files and "all channels"
    files also contain `targetGeom` blocks (under `ikchannels`, or outside any
    actor) that carry no deltas and that Apply can't use.

    Only each channel's own top-level properties are read: nested blocks
    (`keys { ... }`, `trackingScale { ... }`) are skipped, and the `deltas { ... }`
    block, the bulk of the file, is jumped over by the regex engine instead of
    being read line by line.
    """
    channels = []
    stack = []            # header line of each open block, outermost first
    capture = None        # state of the channel being read, else None
    for brace in _BRACE_RE.finditer(text):
        at = brace.start()
        if brace.group() == '{':
            header = _header_before(text, at)
            if capture is not None and len(stack) == capture["depth"]:
                capture["pieces"].append(text[capture["from"]:at])
            stack.append(header)
            if capture is None and len(stack) >= 3 and _opens_channel(stack):
                capture = {
                    "depth": len(stack), "from": brace.end(), "pieces": [],
                    "internal_name": _TARGET_GEOM_HEADER_RE.match(header).group(1).strip(),
                }
            continue

        if not stack:      # stray '}': the file is malformed, keep going
            continue
        if capture is not None and len(stack) == capture["depth"]:
            capture["pieces"].append(text[capture["from"]:at])
            channels.append(_channel_from(capture))
            capture = None
        stack.pop()
        if capture is not None and len(stack) == capture["depth"]:
            capture["from"] = brace.end()
    return channels


def _header_before(text: str, brace_at: int) -> str:
    """The text that names the block opened at `brace_at`: the last non-blank
    line before it (a `{` is usually alone on its own line)."""
    end = brace_at
    while True:
        start = text.rfind("\n", 0, end) + 1
        line = text[start:end].strip()
        if line or start == 0:
            return line
        end = start - 1


def _opens_channel(stack) -> bool:
    return (_TARGET_GEOM_HEADER_RE.match(stack[-1]) is not None
            and stack[-2] == "channels" and _CHANNEL_OWNER_RE.match(stack[-3]) is not None)


def _channel_from(capture) -> ScannedChannel:
    top_level = "\n".join(capture["pieces"])
    name = _NAME_RE.search(top_level)
    display = name.group(1) if name else ""
    if len(display) >= 2 and display[0] == display[-1] == '"':
        display = display[1:-1]
    indexes = _INDEXES_RE.search(top_level)
    binary = _BINARY_RE.search(top_level)
    uses_binary = bool(binary and int(binary.group(1)))
    return ScannedChannel(
        # CR2Parser splits on whitespace, so a double space inside a name ("R bang  length")
        # comes out single-spaced; the scan has to name things the way Apply will.
        internal_name=" ".join(capture["internal_name"].split()),
        display_name=" ".join(display.split()),
        # A binary morph's `indexes` counts deltas that live in the .pmd; its own file has
        # no `d` lines, which is what CR2Parser (and so the main-actor rule) sees.
        delta_count=0 if uses_binary else (int(indexes.group(1)) if indexes else 0),
        uses_binary_morph=uses_binary,
    )


def scan_injection_package(path: str, extra_roots=None) -> dict:
    """List a package's morphs without parsing deltas.

    Returns:
        {
            "morphs": [ScannedMorph],      -- first-seen order
            "unresolved_paths": [str],     -- same meaning as load_injection_package()
            "leaf_paths": [str],           -- every file read
        }
    """
    files = resolve_package_files(path, extra_roots=extra_roots)

    candidates = {}   # internal_name -> [(delta_count, display_name)]
    morphs = {}
    for leaf in files["leaf_paths"]:
        for channel in scan_channels(read_poser_file(leaf)):
            morph = morphs.get(channel.internal_name)
            if morph is None:
                morph = morphs[channel.internal_name] = ScannedMorph(
                    internal_name=channel.internal_name, display_name="",
                    delta_count=0, uses_binary_morph=False,
                )
                candidates[channel.internal_name] = []
            candidates[channel.internal_name].append((channel.delta_count, channel.display_name))
            morph.delta_count += channel.delta_count
            morph.uses_binary_morph = morph.uses_binary_morph or channel.uses_binary_morph
            if leaf not in morph.leaf_paths:
                morph.leaf_paths.append(leaf)

    for internal_name, morph in morphs.items():
        morph.display_name = pick_display_name(candidates[internal_name]) or internal_name

    return {
        "morphs": list(morphs.values()),
        "unresolved_paths": files["unresolved_paths"],
        "leaf_paths": files["leaf_paths"],
    }
