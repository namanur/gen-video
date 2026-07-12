#!/usr/bin/env python3
"""Standalone OpenRouter Dedicated Image API test client.

Loads the OpenRouter credential from a standard dotenv file (OPENROUTER_API_KEY)
at the project root, or from the process environment. Sends a generation request
to the dedicated Image API (POST /api/v1/images), decodes the base64 image
response, detects the media_type, and persists the artifact + sanitized metadata
to visual-agent-test/outputs and visual-agent-test/logs.

Supports text-to-image and image-to-image (via --input-reference).

Security:
  * NEVER logs, prints, or stores the API key.
  * The key is read in-process and placed only in the Authorization header.
  * b64_json (image bytes) is NEVER persisted to metadata files — bytes live
    only in the decoded artifact file. Metadata retains media_type, usage, cost.
  * input_references data URLs are summarized in metadata (source, media_type,
    byte count) — never stored as full base64.
  * Does NOT auto-run: --model and --prompt are required. With --dry-run it
    validates arguments and prints the intended request without calling the API.

Usage:
  # text-to-image dry run
  python3 test_openrouter_image_api.py --model bytedance-seed/seedream-4.5 \
      --prompt "a red panda astronaut, studio lighting" --dry-run

  # image-to-image dry run (local reference file)
  python3 test_openrouter_image_api.py --model bytedance-seed/seedream-4.5 \
      --prompt "make this look like a watercolor painting" \
      --input-reference /path/to/product.jpg --dry-run
"""

import argparse
import base64
import datetime
import json
import mimetypes
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path

# --------------------------------------------------------------------------- #
# Paths
# --------------------------------------------------------------------------- #
SCRIPT_DIR = Path(__file__).resolve().parent
TEST_ROOT = SCRIPT_DIR.parent                  # visual-agent-test/
PROJECT_ROOT = TEST_ROOT.parent                # gen-video/
ENV_FILE = PROJECT_ROOT / ".env"
OUTPUT_DIR = TEST_ROOT / "outputs"
LOG_DIR = TEST_ROOT / "logs"

# --------------------------------------------------------------------------- #
# API endpoints (dedicated Image API, NOT chat completions)
# --------------------------------------------------------------------------- #
API_BASE = "https://openrouter.ai/api/v1"
IMAGES_ENDPOINT = f"{API_BASE}/images"
MODELS_ENDPOINT = f"{API_BASE}/images/models"

# --------------------------------------------------------------------------- #
# media_type -> file extension
# --------------------------------------------------------------------------- #
EXT_MAP = {
    "image/png": ".png",
    "image/jpeg": ".jpg",
    "image/jpg": ".jpg",
    "image/webp": ".webp",
    "image/svg+xml": ".svg",
}
DEFAULT_EXT = ".png"
SECRET_KEYS = {"authorization", "api_key", "apikey", "key", "openrouter_api_key"}


def utc_now() -> str:
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


def media_type_to_ext(media_type: str | None) -> str:
    if not media_type:
        return DEFAULT_EXT
    mt = media_type.lower().split(";")[0].strip()
    return EXT_MAP.get(mt, "." + mt.replace("/", "."))


def load_api_key() -> str:
    """Load OPENROUTER_API_KEY from a standard dotenv file or process env.

    Lookup order:
      1. Process environment variable OPENROUTER_API_KEY (preferred; lets
         ComfyUI / MCP / shells share one exported value).
      2. Standard dotenv file at project root: OPENROUTER_API_KEY=<value>.

    Returns the key string, or "" if not found. NEVER prints the key.
    """
    key = os.environ.get("OPENROUTER_API_KEY", "").strip()
    if key:
        return key

    if ENV_FILE.exists():
        try:
            with open(ENV_FILE, "r", encoding="utf-8") as fh:
                for raw in fh:
                    line = raw.strip()
                    if not line or line.startswith("#") or "=" not in line:
                        continue
                    k, _, v = line.partition("=")
                    k = k.strip()
                    v = v.strip()
                    if len(v) >= 2 and v[0] in "\"'" and v[-1] == v[0]:
                        v = v[1:-1]
                    if k == "OPENROUTER_API_KEY" and v.startswith("sk-"):
                        return v
        except OSError:
            return ""
    return ""


def sanitize(obj) -> dict:
    """Recursively strip any field whose name looks like a secret."""
    if isinstance(obj, dict):
        return {k: sanitize(v) for k, v in obj.items() if k.lower() not in SECRET_KEYS}
    if isinstance(obj, list):
        return [sanitize(v) for v in obj]
    return obj


def strip_b64(obj):
    """Recursively remove any b64_json field.

    Image bytes live ONLY in the decoded artifact file, never in metadata.
    """
    if isinstance(obj, dict):
        return {k: strip_b64(v) for k, v in obj.items() if k != "b64_json"}
    if isinstance(obj, list):
        return [strip_b64(v) for v in obj]
    return obj


def summarize_input_refs(payload: dict) -> dict:
    """Replace data URLs in input_references with summaries.

    Metadata should never contain full base64 data. For local-file references
    (converted to data URLs), retain only source, media_type, and byte count.
    For HTTP URLs, retain the URL as-is.
    """
    p = dict(payload)
    refs = p.get("input_references")
    if not isinstance(refs, list):
        return p
    out = []
    for r in refs:
        if isinstance(r, dict) and isinstance(r.get("image_url"), dict):
            url = r["image_url"].get("url", "")
            if url.startswith("data:"):
                header, _, b64data = url.partition(",")
                media = header.replace("data:", "").replace(";base64", "")
                out.append({
                    "type": r.get("type"),
                    "source": "local_file",
                    "media_type": media,
                    "base64_chars": len(b64data),
                })
            else:
                out.append({
                    "type": r.get("type"),
                    "source": "http_url",
                    "url": url,
                })
        else:
            out.append(r)
    p["input_references"] = out
    return p


def build_input_reference(ref: str) -> dict:
    """Convert a URL or local file path into an input_references entry.

    HTTP(S) URLs are used directly. Local files are read, base64-encoded,
    and wrapped as a data URL (data:<media_type>;base64,...).
    """
    if ref.startswith("http://") or ref.startswith("https://"):
        return {"type": "image_url", "image_url": {"url": ref}}

    path = Path(ref)
    if not path.exists():
        raise FileNotFoundError(f"input reference file not found: {ref}")
    mime, _ = mimetypes.guess_type(str(path))
    if not mime:
        mime = "image/png"
    with open(path, "rb") as fh:
        b64 = base64.b64encode(fh.read()).decode("ascii")
    data_url = f"data:{mime};base64,{b64}"
    return {"type": "image_url", "image_url": {"url": data_url}}


def build_payload(args: argparse.Namespace) -> dict:
    payload = {
        "model": args.model,
        "prompt": args.prompt,
    }
    if args.resolution:
        payload["resolution"] = args.resolution
    if args.aspect_ratio:
        payload["aspect_ratio"] = args.aspect_ratio
    if args.output_format:
        payload["output_format"] = args.output_format
    if args.seed is not None:
        payload["seed"] = args.seed
    if args.n is not None:
        payload["n"] = args.n
    if args.input_references:
        payload["input_references"] = [build_input_reference(r) for r in args.input_references]
    return payload


def write_json(path: Path, obj: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(obj, fh, indent=2)


def do_request(payload: dict, api_key: str) -> tuple[int, dict | str, dict]:
    """POST to the dedicated Image API. Returns (status, parsed_body, meta)."""
    body = json.dumps(payload).encode("utf-8")
    # Key lives ONLY in the header, never logged.
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
    req = urllib.request.Request(IMAGES_ENDPOINT, data=body, headers=headers, method="POST")
    meta = {
        "endpoint": IMAGES_ENDPOINT,
        "method": "POST",
        "request_headers_logged": {"Content-Type": "application/json", "Authorization": "Bearer ***REDACTED***"},
        "request_payload": summarize_input_refs(sanitize(payload)),
        "sent_at_utc": utc_now(),
    }
    try:
        with urllib.request.urlopen(req, timeout=300) as resp:
            raw = resp.read().decode("utf-8", errors="replace")
            status = resp.getcode()
            ct = resp.headers.get("Content-Type", "")
            meta["response_content_type"] = ct
            meta["received_at_utc"] = utc_now()
            try:
                parsed = json.loads(raw)
            except json.JSONDecodeError:
                parsed = {"_raw_text_head": raw[:500]}
                meta["parse_error"] = "response was not valid JSON"
            return status, parsed, meta
    except urllib.error.HTTPError as e:
        raw = ""
        try:
            raw = e.read().decode("utf-8", errors="replace")
        except Exception:
            pass
        meta["received_at_utc"] = utc_now()
        meta["http_error"] = f"HTTP {e.code} {e.reason}"
        try:
            parsed = json.loads(raw) if raw else {}
        except json.JSONDecodeError:
            parsed = {"_raw_text_head": raw[:500]}
        return e.code, parsed, meta
    except urllib.error.URLError as e:
        meta["received_at_utc"] = utc_now()
        meta["network_error"] = str(e.reason)
        return 0, {"_network_error": str(e.reason)}, meta
    except Exception as e:  # last-resort safety net
        meta["received_at_utc"] = utc_now()
        meta["unexpected_error"] = repr(e)
        return -1, {"_unexpected_error": repr(e)}, meta


def run_dry(args: argparse.Namespace, api_key: str) -> int:
    try:
        payload = build_payload(args)
    except FileNotFoundError as e:
        print(f"ERROR: {e}")
        return 2
    key_present = bool(api_key) and api_key.startswith("sk-")
    cred_src = "OPENROUTER_API_KEY env var" if os.environ.get("OPENROUTER_API_KEY") else (
        f"standard dotenv ({ENV_FILE})" if api_key else "NOT FOUND")
    print("=" * 64)
    print("OpenRouter Dedicated Image API — DRY RUN")
    print("=" * 64)
    print(f"target endpoint : {IMAGES_ENDPOINT}")
    print(f"models endpoint : {MODELS_ENDPOINT}")
    print(f"credential src  : {cred_src}")
    print(f"credential ok   : {'yes (sk-*** redacted)' if key_present else 'NO — key missing or malformed'}")
    print(f"output dir      : {OUTPUT_DIR}")
    print(f"sanitized payload (b64_json and data URLs excluded):")
    print(json.dumps(summarize_input_refs(sanitize(payload)), indent=2))
    print("=" * 64)
    print("DRY RUN: no generation request sent. No cost incurred.")
    print("=" * 64)
    return 0 if key_present else 2


def run_generate(args: argparse.Namespace, api_key: str) -> int:
    try:
        payload = build_payload(args)
    except FileNotFoundError as e:
        print(f"ERROR: {e}")
        return 2
    status, parsed, meta = do_request(payload, api_key)

    stamp = datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    safe_model = args.model.replace("/", "_")
    base_name = f"{stamp}_{safe_model}"

    # Persist sanitized response metadata + usage/cost.
    # b64_json is STRIPPED — image bytes live only in the artifact file.
    # input_references data URLs are summarized — no base64 in metadata.
    response_meta = {
        "_meta": {
            "generated_at_utc": utc_now(),
            "model": args.model,
            "http_status": status,
            "endpoint": IMAGES_ENDPOINT,
        },
        "request": summarize_input_refs(sanitize(payload)),
        "response": strip_b64(sanitize(parsed)) if isinstance(parsed, dict) else strip_b64(sanitize({"_raw": str(parsed)[:500]})),
        "usage": (parsed.get("usage") if isinstance(parsed, dict) else None),
        "cost_usd": (parsed.get("usage", {}).get("cost") if isinstance(parsed, dict) and isinstance(parsed.get("usage"), dict) else None),
        "http_status": status,
    }
    write_json(LOG_DIR / f"{base_name}_response_meta.json", response_meta)

    # Handle failures clearly
    if status != 200 or not isinstance(parsed, dict):
        print(f"FAILED: http_status={status}")
        print(f"see metadata: {LOG_DIR / (base_name + '_response_meta.json')}")
        return 1

    data = parsed.get("data")
    if not isinstance(data, list) or not data:
        print("FAILED: response 'data' array missing/empty")
        print(f"see metadata: {LOG_DIR / (base_name + '_response_meta.json')}")
        return 1

    # Decode each returned image
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    saved = []
    for i, img in enumerate(data):
        if not isinstance(img, dict) or "b64_json" not in img:
            print(f"FAILED: image[{i}] missing b64_json")
            continue
        media_type = img.get("media_type")
        ext = media_type_to_ext(media_type)
        try:
            raw_bytes = base64.b64decode(img["b64_json"])
        except Exception as e:
            print(f"FAILED: image[{i}] base64 decode error: {e}")
            continue
        out_path = OUTPUT_DIR / f"{base_name}_{i}{ext}"
        with open(out_path, "wb") as fh:
            fh.write(raw_bytes)
        saved.append({
            "index": i,
            "path": str(out_path),
            "bytes": len(raw_bytes),
            "media_type": media_type,
            "extension": ext,
        })
        print(f"saved image[{i}]: {out_path} ({len(raw_bytes)} bytes, {media_type or 'unknown media_type'}{ext})")

    summary = {
        "_meta": {"generated_at_utc": utc_now(), "model": args.model, "http_status": status},
        "saved_artifacts": saved,
        "usage": parsed.get("usage"),
        "cost_usd": (parsed.get("usage", {}) or {}).get("cost"),
        "response_created": parsed.get("created"),
    }
    write_json(LOG_DIR / f"{base_name}_summary.json", summary)
    print(f"usage: {json.dumps(parsed.get('usage'))}")
    print(f"metadata: {LOG_DIR / (base_name + '_summary.json')}")
    return 0 if saved else 1


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(
        prog="test_openrouter_image_api.py",
        description="OpenRouter Dedicated Image API client (POST /api/v1/images).",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    p.add_argument("--model", required=True, help="Model slug, e.g. bytedance-seed/seedream-4.5")
    p.add_argument("--prompt", required=True, help="Text prompt for image generation")
    p.add_argument("--resolution", default=None, help="Resolution tier: 512 | 1K | 2K | 4K")
    p.add_argument("--aspect-ratio", dest="aspect_ratio", default=None, help="Aspect ratio, e.g. 16:9, 1:1, auto")
    p.add_argument("--output-format", dest="output_format", default=None, help="png | jpeg | webp | svg")
    p.add_argument("--seed", type=int, default=None, help="Deterministic seed (where supported)")
    p.add_argument("--n", type=int, default=None, help="Number of images (1-10 where supported)")
    p.add_argument("--input-reference", dest="input_references", action="append", default=None,
                   help="Reference image URL or local file path (repeatable). Local files are base64-encoded as data URLs.")
    p.add_argument("--dry-run", dest="dry_run", action="store_true", help="Validate args + credential; do NOT call the API")
    args = p.parse_args(argv)

    if not args.model.strip() or not args.prompt.strip():
        p.error("--model and --prompt must be non-empty")

    api_key = load_api_key()

    if args.dry_run:
        return run_dry(args, api_key)

    if not api_key or not api_key.startswith("sk-"):
        print("ERROR: OpenRouter API key not found or malformed. Set OPENROUTER_API_KEY in the process environment or in the .env file.")
        return 2

    return run_generate(args, api_key)


if __name__ == "__main__":
    sys.exit(main())
