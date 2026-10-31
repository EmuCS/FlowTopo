"""Muse Spark: decide what needs edge loops (inventory) and review the result (critique)."""
from . import api

MODEL = "muse-spark-1.3"

SYSTEM = (
    "You are a senior character technical artist reviewing meshes for retopology. "
    "Animation-ready topology needs concentric edge loops around deforming openings "
    "(eyes, mouth), clean boundary loops around protruding parts (nose, ears), and "
    "ring loops around limbs so joints bend cleanly. FlowTopo turns your plan into "
    "guide curves: it finds each feature with SAM 3.1 (text-prompted segmentation on "
    "renders), then cuts rings at geodesic distances around it. Spacing is a multiple "
    "of the feature's own radius. Be concrete and conservative: only list features "
    "that are clearly visible and that matter for deformation."
)

FEATURE = {
    "type": "object",
    "properties": {
        "name": {"type": "string", "description": "short id, e.g. 'eyes', 'mouth', 'nose', 'ears', 'hands'"},
        "sam_prompt": {"type": "string", "description": "short noun phrase for SAM, e.g. 'eye', 'mouth', 'nose', 'ear', 'hand'"},
        "loop_type": {"type": "string", "enum": ["concentric", "boundary", "limb_rings"]},
        "rings_out": {"type": "integer", "description": "rings outside the feature boundary, 0-4"},
        "rings_in": {"type": "integer", "description": "rings inside the feature, 0-2"},
        "spacing": {"type": "number", "description": "ring spacing as a multiple of the feature radius, 0.2-1.0"},
        "why": {"type": "string", "description": "one short sentence, artist's reasoning"},
    },
    "required": ["name", "sam_prompt", "loop_type", "rings_out", "rings_in", "spacing", "why"],
    "additionalProperties": False,
}

INVENTORY_SCHEMA = {
    "type": "object",
    "properties": {
        "subject": {"type": "string", "description": "what the model is, a few words"},
        "category": {"type": "string", "enum": ["human_head", "human_body", "hand", "creature", "stylized_character", "prop", "other"]},
        "symmetric_x": {"type": "boolean", "description": "mirror-symmetric left/right about the vertical centre plane"},
        "density": {"type": "string", "enum": ["low", "medium", "high"]},
        "features": {"type": "array", "items": FEATURE},
        "notes": {"type": "string", "description": "one or two sentences for the artist"},
    },
    "required": ["subject", "category", "symmetric_x", "density", "features", "notes"],
    "additionalProperties": False,
}

ADJUST = {
    "type": "object",
    "properties": {
        "name": {"type": "string"},
        "rings_out": {"type": "integer"},
        "spacing": {"type": "number"},
        "keep": {"type": "boolean", "description": "false to drop this feature's guides"},
    },
    "required": ["name", "rings_out", "spacing", "keep"],
    "additionalProperties": False,
}

CRITIQUE_SCHEMA = {
    "type": "object",
    "properties": {
        "score": {"type": "integer", "description": "animation-readiness 1-10"},
        "good": {"type": "string"},
        "problems": {"type": "string"},
        "adjustments": {"type": "array", "items": ADJUST},
        "density_scale": {"type": "number", "description": "multiply quad size by this (0.7 = denser, 1.3 = coarser), 1.0 = keep"},
        "rerun": {"type": "boolean", "description": "true if the adjustments are worth another pass"},
    },
    "required": ["score", "good", "problems", "adjustments", "density_scale", "rerun"],
    "additionalProperties": False,
}


def _img(path):
    return {"type": "image_url", "image_url": {"url": api.image_data_url(path)}}


def inventory(view_paths):
    content = [{"type": "text", "text": (
        "These are grey renders of one mesh (front, three-quarter, side). Plan the edge "
        "flow guides for animation-ready retopology. List features in priority order. "
        "Use 'concentric' for eyes and mouth, 'boundary' for nose and ears, 'limb_rings' "
        "for hands/feet on full bodies (rings march up the limb from the extremity). "
        "Use one feature entry per kind (e.g. one 'eyes' entry with sam_prompt 'eye' "
        "covers both eyes). If nothing deforms (a rigid prop), return an empty list.")}]
    content += [_img(p) for p in view_paths]
    out, raw = api.spark_chat(
        [{"role": "system", "content": SYSTEM}, {"role": "user", "content": content}],
        model=MODEL, schema=INVENTORY_SCHEMA, schema_name="FlowTopoInventory",
        reasoning_effort="low", max_completion_tokens=6000, tag="spark:inventory")
    return out, raw


def critique(result_paths, plan, metrics_summary):
    content = [{"type": "text", "text": (
        "These renders show the quad remesh produced from your guide plan (wireframe, "
        "close-up of the face/features, and the guide curves in pink). Current plan: "
        + str([{k: f[k] for k in ("name", "rings_out", "spacing")} for f in plan["features"]])
        + ". Measured: " + metrics_summary + ". Judge animation-readiness: do loops "
        "encircle the eyes and mouth, are poles kept out of deforming areas, is density "
        "sensible? Propose small adjustments only if they clearly help.")}]
    content += [_img(p) for p in result_paths]
    out, raw = api.spark_chat(
        [{"role": "system", "content": SYSTEM}, {"role": "user", "content": content}],
        model=MODEL, schema=CRITIQUE_SCHEMA, schema_name="FlowTopoCritique",
        reasoning_effort="low", max_completion_tokens=5000, tag="spark:critique")
    return out, raw
