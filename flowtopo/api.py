"""Meta Model API client: stdlib only, so it runs inside Blender's Python.

Every response is cached on disk (same request -> no second charge) and every
paid call is logged with its cost. A hard budget stops calls before they go out.
"""
import base64
import hashlib
import json
import os
import time
import urllib.error
import urllib.request

BASE_URL = "https://api.meta.ai/v1"

# USD prices from https://dev.meta.ai/docs/pricing-rate-limits (checked 2026-10-30).
PRICES = {
    "muse-spark-1.3": {"in": 1.25e-6, "out": 4.25e-6},
    "muse-spark-1.2": {"in": 1.25e-6, "out": 4.25e-6},
    "sam-3.1": {"per_image": 2.50e-3},
}
BUDGET_USD = float(os.environ.get("FLOWTOPO_BUDGET_USD", "38.0"))


class BudgetExceeded(RuntimeError):
    pass


class ApiError(RuntimeError):
    pass


def data_dir():
    d = os.environ.get("FLOWTOPO_DATA") or os.path.join(os.path.expanduser("~"), ".flowtopo")
    os.makedirs(os.path.join(d, "cache"), exist_ok=True)
    return d


def _read_dotenv_key():
    """Look for MODEL_API_KEY in a .env next to the data dir or in FLOWTOPO_ENV."""
    candidates = [os.environ.get("FLOWTOPO_ENV", "")]
    candidates.append(os.path.join(os.path.dirname(data_dir()), ".env"))
    for path in candidates:
        if path and os.path.isfile(path):
            with open(path, encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if line.startswith("MODEL_API_KEY=") and len(line) > len("MODEL_API_KEY="):
                        return line.split("=", 1)[1].strip().strip('"').strip("'")
    return None


_api_key_override = None


def set_api_key(key):
    global _api_key_override
    _api_key_override = key or None


def api_key():
    key = _api_key_override or os.environ.get("MODEL_API_KEY") or _read_dotenv_key()
    if not key:
        raise ApiError("No MODEL_API_KEY found (add-on preferences, environment, or .env).")
    return key


def spent_usd():
    path = os.path.join(data_dir(), "cost_log.jsonl")
    if not os.path.isfile(path):
        return 0.0
    total = 0.0
    with open(path, encoding="utf-8") as f:
        for line in f:
            if line.strip():
                total += json.loads(line).get("usd", 0.0)
    return total


def _log_cost(entry):
    with open(os.path.join(data_dir(), "cost_log.jsonl"), "a", encoding="utf-8") as f:
        f.write(json.dumps(entry) + "\n")


def _cache_path(endpoint, payload):
    h = hashlib.sha256((endpoint + json.dumps(payload, sort_keys=True)).encode()).hexdigest()
    return os.path.join(data_dir(), "cache", h[:2], h + ".json")


def _post(endpoint, payload, timeout=300, retries=4):
    body = json.dumps(payload).encode()
    req = urllib.request.Request(
        BASE_URL + endpoint,
        data=body,
        headers={
            "Authorization": "Bearer " + api_key(),
            "Content-Type": "application/json",
        },
        method="POST",
    )
    delay = 2.0
    for attempt in range(retries + 1):
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return json.loads(resp.read().decode())
        except urllib.error.HTTPError as e:
            detail = e.read().decode(errors="replace")[:2000]
            if e.code in (429, 500, 502, 503, 504) and attempt < retries:
                time.sleep(delay)
                delay *= 2
                continue
            raise ApiError("HTTP %d on %s: %s" % (e.code, endpoint, detail)) from None
        except urllib.error.URLError as e:
            if attempt < retries:
                time.sleep(delay)
                delay *= 2
                continue
            raise ApiError("Network error on %s: %s" % (endpoint, e)) from None


def _cost_of(model, response):
    p = PRICES.get(model, {})
    if "per_image" in p:
        return p["per_image"]
    u = response.get("usage") or {}
    tin = u.get("prompt_tokens", u.get("input_tokens", 0)) or 0
    tout = u.get("completion_tokens", u.get("output_tokens", 0)) or 0
    return tin * p.get("in", 0) + tout * p.get("out", 0)


def call(endpoint, payload, est_usd, tag=""):
    """POST with cache + budget guard. est_usd is a pessimistic pre-call estimate."""
    path = _cache_path(endpoint, payload)
    if os.path.isfile(path):
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    spent = spent_usd()
    if spent + est_usd > BUDGET_USD:
        raise BudgetExceeded("Budget guard: spent $%.2f, next call ~$%.3f, cap $%.2f" % (spent, est_usd, BUDGET_USD))
    t0 = time.time()
    response = _post(endpoint, payload)
    usd = _cost_of(payload.get("model", ""), response)
    _log_cost({
        "t": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "model": payload.get("model"),
        "endpoint": endpoint,
        "tag": tag,
        "usd": round(usd, 6),
        "usage": response.get("usage"),
        "secs": round(time.time() - t0, 2),
    })
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(response, f)
    return response


def image_data_url(png_path):
    with open(png_path, "rb") as f:
        b64 = base64.b64encode(f.read()).decode()
    ext = os.path.splitext(png_path)[1].lower().lstrip(".")
    mime = "image/jpeg" if ext in ("jpg", "jpeg") else "image/" + ext
    return "data:%s;base64,%s" % (mime, b64)


def spark_chat(messages, model="muse-spark-1.3", schema=None, schema_name="Result",
               reasoning_effort=None, max_completion_tokens=8000, tag=""):
    """Chat completion. Returns (content_str_or_obj, raw_response)."""
    payload = {"model": model, "messages": messages, "max_completion_tokens": max_completion_tokens}
    if reasoning_effort:
        payload["reasoning_effort"] = reasoning_effort
    if schema is not None:
        payload["response_format"] = {
            "type": "json_schema",
            "json_schema": {"name": schema_name, "strict": True, "schema": schema},
        }
    n_images = sum(
        1 for m in messages if isinstance(m.get("content"), list)
        for part in m["content"] if part.get("type") == "image_url"
    )
    est = (2000 + 1600 * n_images) * PRICES[model]["in"] + max_completion_tokens * PRICES[model]["out"]
    raw = call("/chat/completions", payload, est, tag=tag)
    content = raw["choices"][0]["message"]["content"]
    if schema is not None:
        content = json.loads(content)
    return content, raw
