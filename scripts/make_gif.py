"""README GIF: input and FlowTopo result turning side by side (greyscale, no dependencies).

blender --background --factory-startup --python scripts/make_gif.py -- [mesh] [step]
Reads out/demo/<mesh>/tt_input and tt_result (from make_demo.py), writes docs/turntable.gif.
"""
import os
import struct
import sys

import bpy
import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
args = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
name = args[0] if args else "head_realistic"
step = int(args[1]) if len(args) > 1 else 1
DEMO = os.path.join(ROOT, "out", "demo", name)
MARGIN = 30  # px around the head, at full resolution


def full_grey(path):
    img = bpy.data.images.load(path)
    w, h = img.size
    px = np.empty(w * h * 4, np.float32)
    img.pixels.foreach_get(px)
    bpy.data.images.remove(img)
    px = px.reshape(h, w, 4)[::-1, :, :3]
    return px @ np.array([0.2126, 0.7152, 0.0722], np.float32)


def head_box(paths):
    """Union of the head's bounding box over all frames, centred horizontally on the frame."""
    r0 = c0 = 10 ** 9
    r1 = c1 = 0
    for p in paths:
        y = full_grey(p)
        rows, cols = np.nonzero(y > y[0, 0] + 0.05)
        r0, r1 = min(r0, rows.min()), max(r1, rows.max())
        c0, c1 = min(c0, cols.min()), max(c1, cols.max())
    h, w = y.shape
    half = max(w // 2 - c0, c1 - w // 2) + MARGIN  # symmetric about the turntable axis
    r0, r1 = max(r0 - MARGIN, 0), min(r1 + MARGIN, h)
    c0, c1 = max(w // 2 - half, 0), min(w // 2 + half, w)
    return r0 - (r1 - r0) % 2, r1, c0, c1 - (c1 - c0) % 2


def grey(path):
    y = full_grey(path)
    r0, r1, c0, c1 = CROP
    y = y[r0:r1, c0:c1]
    y = y.reshape(y.shape[0] // 2, 2, y.shape[1] // 2, 2).mean((1, 3))  # half size, box filter
    return np.clip(y * 255 + 0.5, 0, 255).astype(np.uint8)


def lzw(data):
    """GIF LZW with 8-bit pixels (same code-size rules as giflib)."""
    clear, eoi = 256, 257
    out, acc, nacc = bytearray(), 0, 0
    st = {"size": 9, "next": 258}
    table = {}

    def emit(code):
        nonlocal acc, nacc
        acc |= code << nacc
        nacc += st["size"]
        while nacc >= 8:
            out.append(acc & 0xFF)
            acc >>= 8
            nacc -= 8
        if st["next"] >= (1 << st["size"]) and st["size"] < 12:
            st["size"] += 1

    emit(clear)
    prefix = data[0]
    for b in data[1:]:
        key = (prefix << 8) | b
        c = table.get(key)
        if c is not None:
            prefix = c
            continue
        emit(prefix)
        if st["next"] >= 4095:
            emit(clear)
            table.clear()
            st["size"], st["next"] = 9, 258
        else:
            table[key] = st["next"]
            st["next"] += 1
        prefix = b
    emit(prefix)
    emit(eoi)
    if nacc:
        out.append(acc & 0xFF)
    return bytes(out)


def write_gif(path, frames, delay_cs):
    h, w = frames[0].shape
    f = open(path, "wb")
    f.write(b"GIF89a" + struct.pack("<HHBBB", w, h, 0xF7, 0, 0))
    f.write(bytes(v for i in range(256) for v in (i, i, i)))
    f.write(b"\x21\xFF\x0BNETSCAPE2.0\x03\x01\x00\x00\x00")  # loop forever
    for fr in frames:
        f.write(struct.pack("<BBBBHB", 0x21, 0xF9, 4, 0, delay_cs, 0) + b"\x00")
        f.write(struct.pack("<BHHHHB", 0x2C, 0, 0, w, h, 0) + b"\x08")
        data = lzw(fr.tobytes())
        for i in range(0, len(data), 255):
            chunk = data[i:i + 255]
            f.write(bytes([len(chunk)]) + chunk)
        f.write(b"\x00")
    f.write(b"\x3B")
    f.close()


names = sorted(os.listdir(os.path.join(DEMO, "tt_result")))[::step]
CROP = head_box([os.path.join(DEMO, d, n) for d in ("tt_input", "tt_result") for n in names])
print("CROP", CROP)
frames = []
for n in names:
    a, b = grey(os.path.join(DEMO, "tt_input", n)), grey(os.path.join(DEMO, "tt_result", n))
    gap = np.full((a.shape[0], 8), a[0, 0], np.uint8)
    frames.append(np.hstack([a, gap, b]))
out = os.path.join(ROOT, "docs", "turntable.gif")
write_gif(out, frames, delay_cs=8 * step)
print("GIF", out, len(frames), "frames", frames[0].shape, "%.1f MB" % (os.path.getsize(out) / 1e6))
