# SPDX-License-Identifier: GPL-3.0-or-later
"""
mac_pict.py
-----------
Decodes the thumbnail Poser stored in a classic Mac OS resource fork (`.rsr`): one `PICT`
resource, a QuickDraw picture. Standard library only, so the child Blender process that writes
the library's asset files can import it (core/library_assets_child.py) and pytest can test it.

Scope is what the owner's library holds, surveyed over all 8,645 `.rsr` files (Phase 7.3d,
docs/handoff-morph-injection.md): every PICT is version 2, almost all 91 x 91, and is a few
header records (HeaderOp, Clip, DefHilite, comments, among them Photoshop's) followed by exactly
one bitmap, in one of four PackBits-packed pixel formats:
  DirectBitsRect, 32-bit, 4 component planes per row (packType 4)   8,033 files
  DirectBitsRect, 16-bit 5-5-5, packed per pixel (packType 3)          257
  DirectBitsRect, 32-bit, 3 component planes per row (packType 4)      183
  PackBitsRect, 8-bit indexed with a colour table                       87
Anything else raises PictError rather than guessing; the caller falls back to a placeholder.
Pillow has no PICT reader and ImageMagick/netpbm are native tools users won't have, so this is
the add-on's own; ImageMagick serves as the reference its output is checked against.
"""

import struct


class PictError(ValueError):
    """The data isn't a PICT this decoder handles."""


# ---------------------------------------------------------------------------
# The resource fork
# ---------------------------------------------------------------------------

def read_resource(raw: bytes, rtype: bytes = b"PICT") -> bytes:
    """The data of the first `rtype` resource in a classic Mac OS resource fork."""
    try:
        data_off, map_off, _data_len, map_len = struct.unpack_from(">IIII", raw, 0)
        rmap = raw[map_off:map_off + map_len]
        type_list_off, = struct.unpack_from(">H", rmap, 24)
        type_list = rmap[type_list_off:]
        n_types = struct.unpack_from(">H", type_list, 0)[0] + 1
        for i in range(n_types):
            kind, _n_minus_1, ref_off = struct.unpack_from(">4sHH", type_list, 2 + 8 * i)
            if kind != rtype:
                continue
            _rid, _name, attr_off, _handle = struct.unpack_from(">hHII", type_list, ref_off)
            start = data_off + (attr_off & 0xFFFFFF)
            size, = struct.unpack_from(">I", raw, start)
            return raw[start + 4:start + 4 + size]
    except struct.error as exc:
        raise PictError(f"not a readable resource fork: {exc}") from None
    raise PictError(f"no {rtype.decode('mac_roman')} resource")


# ---------------------------------------------------------------------------
# PackBits
# ---------------------------------------------------------------------------

def unpack_bits(packed: bytes) -> bytearray:
    """Apple PackBits: a signed count n, then n+1 literal bytes, or -n+1 copies of the next byte."""
    out = bytearray()
    i, end = 0, len(packed)
    while i < end:
        n = packed[i]
        i += 1
        if n < 128:
            out += packed[i:i + n + 1]
            i += n + 1
        elif n > 128:
            out += packed[i:i + 1] * (257 - n)
            i += 1
        # n == 128 is a no-op
    return out


def unpack_words(packed: bytes) -> bytearray:
    """PackBits over 16-bit words (packType 3): counts as above, repeating whole words."""
    out = bytearray()
    i, end = 0, len(packed)
    while i < end:
        n = packed[i]
        i += 1
        if n < 128:
            out += packed[i:i + 2 * (n + 1)]
            i += 2 * (n + 1)
        elif n > 128:
            out += packed[i:i + 2] * (257 - n)
            i += 2
    return out


# ---------------------------------------------------------------------------
# The picture
# ---------------------------------------------------------------------------

# PICT v2 opcodes whose data has a fixed length (bytes after the opcode).
_FIXED = {
    0x0000: 0, 0x0002: 8, 0x0003: 2, 0x0004: 1, 0x0005: 2, 0x0006: 4, 0x0007: 4, 0x0008: 2,
    0x0009: 8, 0x000A: 8, 0x000B: 4, 0x000C: 4, 0x000D: 2, 0x000E: 4, 0x000F: 4, 0x0010: 8,
    0x0011: 2, 0x001A: 6, 0x001B: 6, 0x001C: 0, 0x001D: 6, 0x001E: 0, 0x001F: 6,
    0x00A0: 2, 0x0C00: 24,
}


def _rows(data: bytes, pos: int, row_bytes: int, height: int, words: bool) -> tuple:
    """`height` unpacked rows starting at `pos`; returns (rows, position after them)."""
    rows = []
    for _ in range(height):
        if row_bytes < 8:                            # rows this short are stored unpacked
            rows.append(bytearray(data[pos:pos + row_bytes]))
            pos += row_bytes
            continue
        if row_bytes > 250:
            count, = struct.unpack_from(">H", data, pos)
            pos += 2
        else:
            count = data[pos]
            pos += 1
        packed = data[pos:pos + count]
        pos += count
        rows.append(unpack_words(packed) if words else unpack_bits(packed))
    return rows, pos


def _pixmap(data: bytes, pos: int) -> tuple:
    """Parse a PixMap record (from rowBytes on). Returns (fields, position after it)."""
    (row_bytes, top, left, bottom, right, _ver, pack_type, _pack_size, _hres, _vres,
     _pixel_type, pixel_size, cmp_count, cmp_size, _plane, _table, _reserved) = struct.unpack_from(
        ">HhhhhHHIIIHHHHIII", data, pos)
    if not row_bytes & 0x8000:
        raise PictError("1-bit BitMap, not a PixMap")
    fields = {"row_bytes": row_bytes & 0x3FFF, "width": right - left, "height": bottom - top,
              "pack_type": pack_type, "pixel_size": pixel_size, "cmp_count": cmp_count, "cmp_size": cmp_size}
    return fields, pos + 46


def _direct_bits(data: bytes, pos: int) -> tuple:
    pm, pos = _pixmap(data, pos + 4)                 # skip baseAddr
    pos += 8 + 8 + 2                                 # srcRect, dstRect, transfer mode
    w, h = pm["width"], pm["height"]
    if pm["pixel_size"] == 32 and pm["pack_type"] == 4 and pm["cmp_count"] in (3, 4):
        rows, pos = _rows(data, pos, pm["row_bytes"], h, words=False)
        planes = pm["cmp_count"]
        skip = planes - 3                            # a 4th plane comes first: alpha or padding
        pixels = bytearray()
        for row in rows:
            if len(row) < planes * w:
                raise PictError("short row")
            r, g, b = (row[(skip + c) * w:(skip + c + 1) * w] for c in range(3))
            for x in range(w):
                pixels += bytes((r[x], g[x], b[x]))
        return w, h, pixels
    if pm["pixel_size"] == 16 and pm["pack_type"] == 3:
        rows, pos = _rows(data, pos, pm["row_bytes"], h, words=True)
        pixels = bytearray()
        for row in rows:
            if len(row) < 2 * w:
                raise PictError("short row")
            for x in range(w):
                v = (row[2 * x] << 8) | row[2 * x + 1]
                r, g, b = (v >> 10) & 31, (v >> 5) & 31, v & 31
                pixels += bytes(((r << 3) | (r >> 2), (g << 3) | (g >> 2), (b << 3) | (b >> 2)))
        return w, h, pixels
    raise PictError(f"DirectBitsRect {pm['pixel_size']}-bit packType {pm['pack_type']} "
                    f"cmpCount {pm['cmp_count']} isn't handled")


def _pack_bits_rect(data: bytes, pos: int) -> tuple:
    pm, pos = _pixmap(data, pos)
    if pm["pixel_size"] != 8:
        raise PictError(f"{pm['pixel_size']}-bit indexed PackBitsRect isn't handled")
    _seed, flags, size = struct.unpack_from(">IHH", data, pos)
    pos += 8
    table = [(0, 0, 0)] * 256
    for i in range(size + 1):
        value, r, g, b = struct.unpack_from(">HHHH", data, pos)
        pos += 8
        table[i if flags & 0x8000 else value & 0xFF] = (r >> 8, g >> 8, b >> 8)
    pos += 8 + 8 + 2                                 # srcRect, dstRect, transfer mode
    w, h = pm["width"], pm["height"]
    rows, pos = _rows(data, pos, pm["row_bytes"], h, words=False)
    pixels = bytearray()
    for row in rows:
        if len(row) < w:
            raise PictError("short row")
        for x in range(w):
            pixels += bytes(table[row[x]])
    return w, h, pixels


def decode_pict(data: bytes) -> tuple:
    """(width, height, RGB bytes, rows top to bottom) of a PICT's bitmap. Raises PictError."""
    if len(data) < 16 or data[10:14] != b"\x00\x11\x02\xff":
        raise PictError("not a version 2 PICT")
    pos = 14
    while pos + 2 <= len(data):
        op, = struct.unpack_from(">H", data, pos)
        pos += 2
        if op == 0x009A:
            return _direct_bits(data, pos)
        if op == 0x0098:
            return _pack_bits_rect(data, pos)
        if op == 0x00FF:
            break
        if op in _FIXED:
            pos += _FIXED[op]
        elif op == 0x0001:                           # Clip: a region, its size first
            pos += struct.unpack_from(">H", data, pos)[0]
        elif op == 0x00A1:                           # LongComment: kind, size, data
            pos += 4 + struct.unpack_from(">H", data, pos + 2)[0]
        else:
            raise PictError(f"opcode {op:#06x} before the bitmap isn't handled")
        pos += pos & 1                               # v2 opcodes are word-aligned
    raise PictError("no bitmap in the picture")


def decode_rsr(path: str) -> tuple:
    """(width, height, RGB bytes top-down) of the PICT thumbnail in a `.rsr` file."""
    with open(path, "rb") as fh:
        return decode_pict(read_resource(fh.read()))
