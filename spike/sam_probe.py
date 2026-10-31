"""Spike: render the scan from the front, ask SAM for 'eye', dump the raw output.

blender --background --python spike/sam_probe.py
"""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.environ.setdefault("FLOWTOPO_DATA", os.path.join(ROOT, "out"))

import bpy  # noqa: E402

from flowtopo import api, render  # noqa: E402

bpy.ops.wm.read_homefile(use_empty=True)
bpy.ops.wm.obj_import(filepath=os.path.join(ROOT, "assets", "meshes", "lee_scan_messy.obj"))
obj = bpy.context.selected_objects[0]
out_dir = os.path.join(ROOT, "out", "spike_sam")
recs = render.render_views(obj, out_dir, views=[("front", 0, 5)])
payload = {
    "model": "sam-3.1",
    "input": [{"type": "message", "role": "user", "content": [
        {"type": "input_text", "text": "eye"},
        {"type": "input_image", "image_url": api.image_data_url(recs[0]["path"])},
    ]}],
    "metadata": {"mask_encoding": "one_bit", "include_confidence": "true"},
}
resp = api.call("/responses", payload, est_usd=0.0025, tag="probe")
import json  # noqa: E402
with open(os.path.join(out_dir, "raw.json"), "w") as f:
    json.dump(resp, f, indent=1)
print("KEYS", list(resp.keys()))
print("SPENT", api.spent_usd())
