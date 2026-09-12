# SPDX-License-Identifier: MIT
"""
poser_io.py
-----------
Shared I/O utilities for reading Poser file formats.

All Poser text-based formats (CR2, PP2, HR2, LT2, CM2, PZ2) share the same
token-based syntax and can be compressed with gzip into their .z counterparts
(.crz, .ppz, .hrz, .ltz, .cmz, .p2z).

This module provides a single entry point -- read_poser_file() -- that returns
decoded text regardless of whether the source file is plain-text or
gzip-compressed.  All parsers should call this instead of open() directly.
"""

from __future__ import annotations

import gzip
import os


COMPRESSED_EXTENSIONS = frozenset({
    '.crz', '.ppz', '.hrz', '.ltz', '.cmz', '.p2z',
})

POSER_EXTENSIONS = frozenset({
    '.cr2', '.crz', '.pp2', '.ppz', '.hr2', '.hrz',
    '.lt2', '.ltz', '.cm2', '.cmz', '.pz2', '.p2z',
})

POSER_GLOB = "*.cr2;*.crz;*.pp2;*.ppz;*.hr2;*.hrz;*.lt2;*.ltz;*.cm2;*.cmz"


def read_poser_file(path: str) -> str:
    """
    Read a Poser file and return its full text content.
    Transparently decompresses gzip-compressed variants.
    Falls back to plain-text if gzip fails (common Poser quirk).
    """
    if not os.path.isfile(path):
        raise FileNotFoundError(f"Poser file not found: {path}")

    ext = os.path.splitext(path)[1].lower()

    if ext in COMPRESSED_EXTENSIONS:
        try:
            with gzip.open(path, 'rt', encoding='utf-8', errors='replace') as fh:
                return fh.read()
        except (OSError, EOFError):
            pass

    with open(path, 'r', encoding='utf-8', errors='replace') as fh:
        return fh.read()


def is_compressed(path: str) -> bool:
    return os.path.splitext(path)[1].lower() in COMPRESSED_EXTENSIONS


def plain_equivalent(path: str) -> str:
    _map = {
        '.crz': '.cr2', '.ppz': '.pp2', '.hrz': '.hr2',
        '.ltz': '.lt2', '.cmz': '.cm2', '.p2z': '.pz2',
    }
    base, ext = os.path.splitext(path)
    return base + _map.get(ext.lower(), ext)
