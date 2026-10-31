# Copyright (c) Meta Platforms, Inc. and affiliates. All Rights Reserved.
# Adapted from meta-sam-parser 0.1.0 (_mask_codec.py), used under the SAM License
# (see LICENSE-SAM.txt). Only the one_bit decoder is kept; canonical-form checks removed.
"""Decode SAM 3.1 'one_bit' mask payloads ('!' + base85 range-coded bitmap)."""
from array import array

_EXCLUDED = frozenset({'"', "\\", ",", ";", "<", ">", "|"})
_ALPHABET = "".join(c for code in range(0x21, 0x7F) if (c := chr(code)) not in _EXCLUDED)
_RADIX = 85
_PREFIX_LENGTH = 5
_VALUES = [-1] * 128
for _i, _c in enumerate(_ALPHABET):
    _VALUES[ord(_c)] = _i

_TOP = 1 << 24
_BOTTOM = 1 << 16
_MASK_32 = 0xFFFFFFFF
_INCREMENT = 14
_COUNT_LIMIT = 4096
_CONTEXT_COUNT = 1 << 12
_PADDING = 2


def _unpack(payload):
    length = 0
    for ch in payload[:_PREFIX_LENGTH]:
        length = length * _RADIX + _VALUES[ord(ch)]
    remainder = length % 4
    out = bytearray(length)
    src, dst, full = _PREFIX_LENGTH, 0, length - remainder
    while dst < full:
        v = 0
        for i in range(5):
            v = v * _RADIX + _VALUES[ord(payload[src + i])]
        src += 5
        out[dst] = (v >> 24) & 0xFF
        out[dst + 1] = (v >> 16) & 0xFF
        out[dst + 2] = (v >> 8) & 0xFF
        out[dst + 3] = v & 0xFF
        dst += 4
    if remainder:
        v = 0
        for i in range(5):
            v = v * _RADIX + (_VALUES[ord(payload[src + i])] if i < remainder + 1 else _RADIX - 1)
        for i in range(remainder):
            out[dst + i] = (v >> (24 - i * 8)) & 0xFF
    return bytes(out)


class _RangeDecoder:
    __slots__ = ("_code", "_input", "_low", "_pos", "_range", "_scaled")

    def __init__(self, data):
        self._input = data
        self._pos = 0
        self._low = 0
        self._range = _MASK_32
        self._code = 0
        self._scaled = 0
        for _ in range(4):
            self._code = ((self._code << 8) | self._read()) & _MASK_32

    def _read(self):
        if self._pos >= len(self._input):
            return 0
        v = self._input[self._pos]
        self._pos += 1
        return v

    def frequency(self, total):
        self._scaled = self._range // total
        v = ((self._code - self._low) & _MASK_32) // self._scaled
        return total - 1 if v >= total else v

    def decode(self, cumulative, freq):
        self._low = (self._low + self._scaled * cumulative) & _MASK_32
        self._range = self._scaled * freq
        while True:
            if ((self._low ^ ((self._low + self._range) & _MASK_32)) & _MASK_32) < _TOP:
                pass
            elif self._range < _BOTTOM:
                self._range = (-self._low & _MASK_32) & (_BOTTOM - 1)
            else:
                break
            self._code = ((self._code << 8) | self._read()) & _MASK_32
            self._low = (self._low << 8) & _MASK_32
            self._range = (self._range << 8) & _MASK_32


def decode_one_bit(payload, width, height):
    """payload starts with '!'. Returns row-major bytes of 0/1, length width*height."""
    dec = _RangeDecoder(_unpack(payload[1:]))
    counts = array("H", [1]) * (_CONTEXT_COUNT * 2)
    pw = width + 2 * _PADDING
    padded = bytearray(pw * (height + _PADDING))
    out = bytearray(width * height)
    for y in range(height):
        row = (y + _PADDING) * pw + _PADDING
        for x in range(width):
            p = row + x
            pr = p - pw
            er = p - 2 * pw
            ctx = (padded[p - 1] | (padded[p - 2] << 1) | (padded[pr - 2] << 2) | (padded[pr - 1] << 3)
                   | (padded[pr] << 4) | (padded[pr + 1] << 5) | (padded[pr + 2] << 6)
                   | (padded[er - 2] << 7) | (padded[er - 1] << 8) | (padded[er] << 9)
                   | (padded[er + 1] << 10) | (padded[er + 2] << 11))
            off = ctx << 1
            zero = counts[off]
            one = counts[off + 1]
            bit = 0 if dec.frequency(zero + one) < zero else 1
            if bit == 0:
                dec.decode(0, zero)
            else:
                dec.decode(zero, one)
            padded[p] = bit
            out[y * width + x] = bit
            counts[off + bit] = (counts[off + bit] + _INCREMENT) & 0xFFFF
            if counts[off] + counts[off + 1] >= _COUNT_LIMIT:
                counts[off] = (counts[off] >> 1) or 1
                counts[off + 1] = (counts[off + 1] >> 1) or 1
    return bytes(out)
