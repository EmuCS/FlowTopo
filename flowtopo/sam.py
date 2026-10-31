"""SAM 3.1 via the Meta Model API: text-prompted segmentation of one image.

Wire format (https://dev.meta.ai/docs/sam/reading-segmentation):
  <0f>0<|box;x1=..;y1=..;x2=..;y2=..;w=..;h=..;c=..|><|mask;x=..;y=..;c=..;data=H,W,!payload|>,1<|box...
Mask payload is base85; '!' = one_bit, '~' = lossless.
"""
import re

from . import api, sam_codec

_REC = re.compile(r"(\d+)<\|box;([^|]*)\|>\s*<\|mask;([^|]*)\|>")


def _fields(s):
    out = {}
    for part in s.split(";"):
        if "=" in part:
            k, v = part.split("=", 1)
            out[k] = v
    return out


def _output_text(resp):
    if isinstance(resp.get("output_text"), str):
        return resp["output_text"]
    texts = []
    for item in resp.get("output", []) or []:
        for c in item.get("content", []) or []:
            if c.get("type") in ("output_text", "text") and isinstance(c.get("text"), str):
                texts.append(c["text"])
    return "".join(texts)


def decode_mask(data):
    """'H,W,<enc>payload' -> (H, W, row-major bytes of 0/1). The raster is box-local."""
    h, w, rest = data.split(",", 2)
    h, w = int(h), int(w)
    if rest[0] != "!":
        raise NotImplementedError("lossless '~' masks not supported; request one_bit")
    return h, w, sam_codec.decode_one_bit(rest, w, h)


def segment(png_path, prompt, tag=""):
    """Return list of {id, box:(x1,y1,x2,y2), score, mask:(x, y, h, w, bits)}.

    mask x, y are the absolute top-left pixel of the box-local raster.
    """
    payload = {
        "model": "sam-3.1",
        "input": [{
            "type": "message",
            "role": "user",
            "content": [
                {"type": "input_text", "text": prompt},
                {"type": "input_image", "image_url": api.image_data_url(png_path)},
            ],
        }],
        "metadata": {"mask_encoding": "one_bit", "include_confidence": "true"},
    }
    resp = api.call("/responses", payload, est_usd=0.0025, tag=tag or "sam:" + prompt)
    text = _output_text(resp)
    out = []
    for m in _REC.finditer(text):
        box, mask = _fields(m.group(2)), _fields(m.group(3))
        bx = tuple(int(float(box[k])) for k in ("x1", "y1", "x2", "y2"))
        x = bx[0] + int(float(mask.get("x", 0)))
        y = bx[1] + int(float(mask.get("y", 0)))
        h, w, bits = decode_mask(mask["data"])
        out.append({
            "id": int(m.group(1)),
            "box": bx,
            "frame": (int(float(box.get("w", 0))), int(float(box.get("h", 0)))),
            "score": float(box.get("c", mask.get("c", 1.0))),
            "mask": (x, y, h, w, bits),
        })
    return out, text
