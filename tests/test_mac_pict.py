# SPDX-License-Identifier: GPL-3.0-or-later
"""Tests for core/mac_pict.py (Phase 7.3d): the PICT thumbnails in old Mac `.rsr` resource forks.

Synthetic pictures are built here in each of the four formats the owner's library uses, so a
decode can be checked against the exact pixels that went in. A real-file check compares the
decoder with ImageMagick when both the content drive and `convert` are present.
"""

import os
import shutil
import struct
import subprocess

import pytest

from core.mac_pict import PictError, decode_pict, decode_rsr, read_resource, unpack_bits, unpack_words

_CONTENT = "/run/media/jess-green/236a91c9-2598-40dd-b968-9c0f0be8a903/Artwork/Poser Content"


def pack_bits(data: bytes) -> bytes:
    """A simple PackBits encoder: runs of 3+ equal bytes, literals otherwise."""
    out, i = bytearray(), 0
    while i < len(data):
        run = 1
        while i + run < len(data) and data[i + run] == data[i] and run < 128:
            run += 1
        if run >= 3:
            out += bytes((257 - run,)) + data[i:i + 1]
            i += run
            continue
        j = i
        while j < len(data) and j - i < 128 and not (j + 2 < len(data) and data[j] == data[j + 1] == data[j + 2]):
            j += 1
        out += bytes((j - i - 1,)) + data[i:j]
        i = j
    return bytes(out)


def pack_words(data: bytes) -> bytes:
    """PackBits over 16-bit words, literals only (enough to exercise the decoder)."""
    out = bytearray()
    for start in range(0, len(data), 256):
        chunk = data[start:start + 256]
        out += bytes((len(chunk) // 2 - 1,)) + chunk
    return bytes(out)


def _pixmap(row_bytes, w, h, pack_type, pixel_type, pixel_size, cmp_count, cmp_size):
    return struct.pack(">HhhhhHHIIIHHHHIII", row_bytes | 0x8000, 0, 0, h, w, 0, pack_type, 0,
                       0x480000, 0x480000, pixel_type, pixel_size, cmp_count, cmp_size, 0, 0, 0)


def _rows(rows, row_bytes, packer):
    out = bytearray()
    for row in rows:
        packed = packer(row)
        out += struct.pack(">H", len(packed)) if row_bytes > 250 else bytes((len(packed),))
        out += packed
    return bytes(out)


def make_pict(w, h, bitmap: bytes, comment=True) -> bytes:
    """A version 2 PICT: header record, a Photoshop-style comment, a clip, then the bitmap."""
    body = struct.pack(">H", 0x0011) + b"\x02\xff" + struct.pack(">H", 0x0C00) + b"\xff\xfe" + b"\0" * 22
    if comment:
        note = b"8BIM" + b"\0" * 18
        body += struct.pack(">HHH", 0x00A1, 498, len(note)) + note
    body += struct.pack(">HH", 0x0001, 10) + struct.pack(">hhhh", 0, 0, h, w)
    body += bitmap
    if len(body) % 2:
        body += b"\0"
    body += struct.pack(">H", 0x00FF)
    return struct.pack(">Hhhhh", 0, 0, 0, h, w) + body


def direct_32(rgb_rows, w, h, planes=4) -> bytes:
    row_bytes = w * 4
    rows = []
    for row in rgb_rows:
        r, g, b = bytes(row[0::3]), bytes(row[1::3]), bytes(row[2::3])
        rows.append((b"\0" * w if planes == 4 else b"") + r + g + b)
    rects = struct.pack(">hhhh", 0, 0, h, w) * 2 + struct.pack(">H", 0)
    return (struct.pack(">HI", 0x009A, 0xFF) + _pixmap(row_bytes, w, h, 4, 16, 32, planes, 8) + rects
            + _rows(rows, row_bytes, pack_bits))


def direct_16(rgb_rows, w, h) -> bytes:
    row_bytes = w * 2
    rows = []
    for row in rgb_rows:
        words = bytearray()
        for x in range(w):
            r, g, b = (c >> 3 for c in row[3 * x:3 * x + 3])
            words += struct.pack(">H", (r << 10) | (g << 5) | b)
        rows.append(bytes(words))
    rects = struct.pack(">hhhh", 0, 0, h, w) * 2 + struct.pack(">H", 0)
    return (struct.pack(">HI", 0x009A, 0xFF) + _pixmap(row_bytes, w, h, 3, 16, 16, 3, 5) + rects
            + _rows(rows, row_bytes, pack_words))


def indexed_8(index_rows, palette, w, h) -> bytes:
    row_bytes = w
    table = struct.pack(">IHH", 0, 0, len(palette) - 1)
    for i, (r, g, b) in enumerate(palette):
        table += struct.pack(">HHHH", i, r * 257, g * 257, b * 257)
    rects = struct.pack(">hhhh", 0, 0, h, w) * 2 + struct.pack(">H", 0)
    return (struct.pack(">H", 0x0098) + _pixmap(row_bytes, w, h, 0, 0, 8, 1, 8) + table + rects
            + _rows([bytes(r) for r in index_rows], row_bytes, pack_bits))


def resource_fork(pict: bytes) -> bytes:
    """A minimal classic Mac resource fork holding one PICT, id 128."""
    data = struct.pack(">I", len(pict)) + pict
    type_list = struct.pack(">H", 0) + struct.pack(">4sHH", b"PICT", 0, 10)
    ref = struct.pack(">hHII", 128, 0xFFFF, 0, 0)
    rmap = b"\0" * 24 + struct.pack(">HH", 28, 28 + len(type_list) + len(ref)) + type_list + ref
    data_off = 256
    map_off = data_off + len(data)
    header = struct.pack(">IIII", data_off, map_off, len(data), len(rmap))
    return header + b"\0" * (data_off - 16) + data + rmap


def _gradient(w, h):
    rows = []
    for y in range(h):
        row = bytearray()
        for x in range(w):
            row += bytes(((x * 7) % 256, (y * 11) % 256, 200 if (x // 4) % 2 else 40))
        rows.append(row)
    return rows


# ---- PackBits -------------------------------------------------------------------------------

def test_unpack_bits_handles_literals_runs_and_the_no_op():
    assert unpack_bits(bytes((2, 1, 2, 3, 0xFE, 9, 0x80, 0, 7))) == bytes((1, 2, 3, 9, 9, 9, 7))
    data = bytes(range(10)) + b"\x05" * 40 + bytes(range(3))
    assert unpack_bits(pack_bits(data)) == data


def test_unpack_words_repeats_whole_words():
    assert unpack_words(bytes((0, 0x12, 0x34, 0xFE, 0xAB, 0xCD))) == bytes((0x12, 0x34) + (0xAB, 0xCD) * 3)


# ---- the four formats -------------------------------------------------------------------------

@pytest.mark.parametrize("w,h", [(91, 91), (5, 3)])
def test_32_bit_four_planes(w, h):
    rows = _gradient(w, h)
    assert decode_pict(make_pict(w, h, direct_32(rows, w, h))) == (w, h, b"".join(rows))


def test_32_bit_three_planes():
    rows = _gradient(91, 91)
    assert decode_pict(make_pict(91, 91, direct_32(rows, 91, 91, planes=3))) == (91, 91, b"".join(rows))


def test_16_bit_widens_five_bits_to_the_full_range():
    rows = _gradient(91, 91)
    w, h, rgb = decode_pict(make_pict(91, 91, direct_16(rows, 91, 91)))
    expected = bytes(((c >> 3) << 3) | ((c >> 3) >> 2) for row in rows for c in row)
    assert (w, h, rgb) == (91, 91, expected)
    white = [bytearray(b"\xff\xff\xff" * 4)]
    assert decode_pict(make_pict(4, 1, direct_16(white, 4, 1)))[2] == b"\xff" * 12   # 31 -> 255, not 248


def test_8_bit_indexed_with_a_colour_table():
    palette = [(0, 0, 0), (255, 0, 0), (0, 128, 255), (250, 250, 250)]
    index_rows = [[(x + y) % 4 for x in range(91)] for y in range(91)]
    w, h, rgb = decode_pict(make_pict(91, 91, indexed_8(index_rows, palette, 91, 91)))
    assert (w, h) == (91, 91)
    assert rgb == b"".join(bytes(palette[i]) for row in index_rows for i in row)


def test_a_picture_without_a_comment_and_from_a_resource_fork(tmp_path):
    rows = _gradient(12, 9)
    pict = make_pict(12, 9, direct_32(rows, 12, 9), comment=False)
    path = tmp_path / "Thumb.rsr"
    path.write_bytes(resource_fork(pict))
    assert read_resource(path.read_bytes()) == pict
    assert decode_rsr(str(path)) == (12, 9, b"".join(rows))


# ---- refusals ---------------------------------------------------------------------------------

def test_what_it_does_not_handle_is_refused_not_guessed():
    with pytest.raises(PictError):
        decode_pict(b"\0" * 10 + b"\x11\x01")                          # PICT version 1
    with pytest.raises(PictError):
        read_resource(b"\0" * 8)                                         # not a resource fork
    bad_op = make_pict(4, 4, struct.pack(">H", 0x8200) + b"\0" * 8)      # QuickTime-compressed
    with pytest.raises(PictError, match="0x8200"):
        decode_pict(bad_op)
    short = bytearray(make_pict(91, 91, direct_32(_gradient(91, 91), 91, 91)))
    with pytest.raises((PictError, struct.error)):
        decode_pict(bytes(short[:len(short) // 2]))                      # damaged: the data runs out


@pytest.mark.skipif(not (os.path.isdir(_CONTENT) and shutil.which("convert")),
                    reason="needs the Poser content drive and ImageMagick")
def test_real_thumbnails_match_imagemagick(tmp_path):
    samples = [
        "Custom Character Sets/Runtime/libraries/Character/BelBel/BelBelPVPreset.rsr",        # 32-bit, 4 planes
        "Scenes, Props and Objects/Runtime/libraries/Pose/MAT DAZ FurniturePP/LB_WhiteChair.rsr",  # + comment
        "Clothing/Runtime/libraries/pose/BVH MATS/Fabric04.RSR",                                 # 8-bit indexed
    ]
    # 16-bit ones differ from ImageMagick on purpose: it widens 5 bits by shifting (31 -> 248), this
    # decoder replicates the top bits (31 -> 255); test_16_bit_widens_five_bits_to_the_full_range.
    for rel in samples:
        path = os.path.join(_CONTENT, rel)
        pict = read_resource(open(path, "rb").read())
        w, h, rgb = decode_pict(pict)
        f = tmp_path / "x.pict"
        f.write_bytes(b"\0" * 512 + pict)
        theirs = subprocess.run(["convert", str(f), "-alpha", "off", "-crop", f"{w}x{h}+0+0", "+repage",
                                 "-depth", "8", "rgb:-"], capture_output=True, check=True).stdout
        assert rgb == theirs, rel
