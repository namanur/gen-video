# Visual Agent Test — OpenRouter Dedicated Image API Audit

> Status: preparation complete (rev 2). **No paid image-generation call has been made.**
> Fixes applied: (1) `.env` converted to standard dotenv `OPENROUTER_API_KEY=...`;
> (2) `b64_json` stripped from all persisted metadata — image bytes live only in
> artifact files; (3) `--input-reference` support added for image-to-image testing.
> Three dry-runs pass. Awaiting user approval for one minimal paid image-editing
> call (see [First Paid Test Recommendation](#first-paid-test-recommendation)).

---

# OpenRouter Dedicated Image API Audit

## Existing Credential Configuration

The OpenRouter credential was located, inspected, and **normalized into standard dotenv format** without ever exposing its value.

| Item | Finding |
| --- | --- |
| Absolute path of `.env` | `/home/naman/Documents/gen-video/.env` |
| File format (current) | **Standard dotenv.** `OPENROUTER_API_KEY=<value>` on a single line, plus comment lines with reference URLs. |
| File format (original) | Custom notes file with `label : value` lines (e.g. `llm api : sk-...`). Converted to standard dotenv during this task. |
| Variable name | `OPENROUTER_API_KEY` |
| Key value | `sk-…` (73 chars). **Never printed, copied, logged, or stored.** |
| Other entries | Comment lines with the OpenRouter docs and announcement URLs (not parsed by dotenv). |
| Auto-loaded? | Any standard dotenv loader (python-dotenv, docker --env-file, direnv, etc.) can load this file. No custom parser needed. |
| Exported into process env? | Not automatically by the file itself. Consumers either (a) use a standard dotenv loader, or (b) receive `OPENROUTER_API_KEY` exported by a parent process / launcher. |
| Reusable by a standalone Image API test script? | **Yes.** The test script reads `OPENROUTER_API_KEY` from the process env first, then falls back to a standard dotenv parse of the `.env` file. No custom format to understand. |
| OpenRouter base URL configured? | **No.** The API base `https://openrouter.ai/api/v1` is a fixed public constant, hardcoded into the test script. |
| OpenRouter models configured for LLM usage? | **None.** The `.env` contains no model IDs. No LLM client/config code exists in `gen-video/`. |

**Conclusion:** the credential interface is now a standard `OPENROUTER_API_KEY=...` dotenv line. Every consumer (test script, OpenCord, ComfyUI, MCP) reads the same well-known environment variable. No custom secret-loading convention exists.

---

## Dedicated Image API Architecture

OpenRouter now provides a **dedicated Image API** that is distinct from image generation through chat completions. New image models are added exclusively to the dedicated Image API; chat-completions image generation still exists for legacy models but is not the path used here.

```text
OpenCord / ComfyUI Workflow / Test Script
                  |
        read OPENROUTER_API_KEY (single source of truth)
                  |
                  v
       POST https://openrouter.ai/api/v1/images
       (Authorization: Bearer <key>; Content-Type: application/json)
                  |
                  v
             OpenRouter  ->  Provider (e.g. ByteDance Seed)
                  |
                  v
       { data:[{ b64_json, media_type }], usage:{ cost } }
                  |
                  v
       Decode base64  ->  detect media_type  ->  pick extension
                  |
                  v
       visual-agent-test/outputs/<timestamp>_<model>.<ext>
                  |
                  v
       visual-agent-test/logs/<...>_summary.json  (usage + cost + sanitized meta)
```

**Key architectural facts (from official docs + verified discovery):**

- **Generation endpoint:** `POST https://openrouter.ai/api/v1/images`
- **Model discovery endpoint:** `GET https://openrouter.ai/api/v1/images/models`
- **Per-endpoint discovery:** `GET https://openrouter.ai/api/v1/images/models/{id}/endpoints`
- **Authentication:** `Authorization: Bearer <OPENROUTER_API_KEY>`. Discovery is **public** (works without auth); generation **requires** the key.
- **Required headers:** `Authorization: Bearer <key>` and `Content-Type: application/json`.
- **Request schema:** `{ model, prompt, n?, resolution?, aspect_ratio?, size?, quality?, output_format?, background?, output_compression?, seed?, stream?, input_references?, provider.options? }`. `model` and `prompt` are required.
- **Response schema:** `{ created, data:[{ b64_json, media_type? }], usage:{ prompt_tokens, completion_tokens, total_tokens, cost? } }`.
- **Image bytes:** returned as `b64_json` (base64-encoded image bytes). For SVG (Recraft vector models), the SVG markup is base64-encoded in `b64_json`.
- **Output media type:** the `media_type` field (e.g. `image/png`, `image/jpeg`, `image/webp`, `image/svg+xml`). It is present whenever the format is identifiable and omitted only when it cannot be determined.
- **Usage & cost:** the `usage` object reports `prompt_tokens`, `completion_tokens`, `total_tokens`, and `cost` (USD). `cost` matches the endpoint pricing record.
- **Reference images / image-to-image:** `input_references` array of `{ type:"image_url", image_url:{ url } }`. URLs may be HTTPS or base64 data URLs. Seedream 4.5 accepts 0–14 references.
- **Multiple images:** `n` (1–10 where supported). Seedream 4.5 supports `n` 1–10.
- **Streaming:** native SSE via `stream:true` for endpoints where `supports_streaming:true`. Event types: `image_generation.partial_image`, `image_generation.completed`, `error`; stream ends with `data: [DONE]`. Seedream 4.5 does **not** support streaming.
- **Billing:** **all-or-nothing**. A completed generation is billed in full; a failed or cancelled generation is **not** billed and returns `502 Bad Gateway`. Partial streaming previews incur **no** partial charge; an early-terminated stream is billed as a failed generation (nothing).
- **Provider-specific options:** passed under `provider.options[<provider_slug>]`; accepted keys are listed in each endpoint's `allowed_passthrough_parameters`. Seedream 4.5 allows **no** passthrough parameters.
- **Capability descriptors:** `supported_parameters` uses typed descriptors — `enum` (`{type,values}`), `range` (`{type,min,max}`), `boolean` (`{type}`). An absent key means the parameter is unsupported by that endpoint.
- **Dedicated Image API vs chat completions:** the dedicated API (`/api/v1/images`) is a separate surface from chat completions. The docs explicitly recommend switching `openai/gpt-5-image*` usage to dedicated image models because the LLM-routed chat variants don't expose the full parameter set and may incur extra inference cost.

---

## Discovery Results

`GET https://openrouter.ai/api/v1/images/models` returned **HTTP 200 both with and without** the Authorization header (identical 27,272-byte body). Discovery is public; the existing key works for it.

**39 image models** are currently available. Sanitized copy saved to:
`visual-agent-test/logs/openrouter-image-models.json`

Relevant highlights (input/output modalities, streaming, parameters):

| Model ID | In → Out | Stream | Supported parameters |
| --- | --- | --- | --- |
| `bytedance-seed/seedream-4.5` | image,text → image | no | resolution, aspect_ratio, n, input_references, seed |
| `google/gemini-3.1-flash-image` | image,text → image,text | no | resolution, aspect_ratio, n, input_references |
| `google/gemini-3.1-flash-lite-image` | image,text → image,text | no | resolution, aspect_ratio, n, input_references |
| `google/gemini-3-pro-image` | image,text → image,text | no | resolution, aspect_ratio, n, input_references |
| `google/gemini-2.5-flash-image` | image,text → image,text | no | aspect_ratio, n, input_references |
| `openai/gpt-image-1` | text,image → image | yes | quality, background, n, input_references, output_compression |
| `openai/gpt-image-1-mini` | text,image → image | yes | quality, background, n, input_references, output_compression |
| `openai/gpt-image-2` | text,image → image | yes | quality, background, n, input_references, output_compression |
| `openai/gpt-5-image` | file,image,text → image,text | yes | quality, background, n, input_references, output_compression |
| `openai/gpt-5-image-mini` | file,image,text → image,text | yes | quality, background, n, input_references, output_compression |
| `openai/gpt-5.4-image-2` | image,text,file → image,text | yes | quality, background, n, input_references, output_compression |
| `black-forest-labs/flux.2-pro` | text,image → image | no | output_format, n, input_references, seed |
| `black-forest-labs/flux.2-max` | text,image → image | no | output_format, n, input_references, seed |
| `black-forest-labs/flux.2-flex` | text,image → image | no | output_format, n, input_references, seed |
| `black-forest-labs/flux.2-klein-4b` | text,image → image | no | output_format, n, input_references, seed |
| `microsoft/mai-image-2.5` | text,image → image | no | aspect_ratio, n, input_references |
| `x-ai/grok-imagine-image-quality` | text,image → image | no | resolution, aspect_ratio, n, input_references |
| `sourceful/riverflow-v2.5-pro` | text,image → image | no | resolution, output_format, background, n, input_references |
| `recraft/recraft-v4.1-pro` (and v4/v4.1 variants, vector/utility) | text,image → image | no | n, input_references |
| `openrouter/auto` | text,image,audio,file,video → text,image | no | (none — router-managed) |

**Discovery conclusions:**
- Authentication is **not** required for model discovery; the existing key works.
- Every model exposes a typed `supported_parameters` map plus an `endpoints` link for per-provider granularity.
- Streaming is currently limited to OpenAI GPT-image models (`supports_streaming:true`).
- Image-to-image is universally available via `input_references` (capacity ranges differ: e.g. Seedream 0–14, some models fewer).
- `seed` is supported by Seedream 4.5 and the FLUX.2 family; not by Google/OpenAI/Recraft/Sourceful image models.

---

## Seedream 4.5 Endpoint Audit

`GET https://openrouter.ai/api/v1/images/models/bytedance-seed/seedream-4.5/endpoints` returned **HTTP 200** (614 bytes; identical with/without auth). Sanitized copy saved to:
`visual-agent-test/logs/seedream-4.5-endpoints.json`

| Item | Finding |
| --- | --- |
| Available? | **Yes.** Present in the models list and in the per-endpoint record. |
| Model ID | `bytedance-seed/seedream-4.5` |
| Provider name | `Seed` |
| Provider slug | `seed` |
| Provider tag | `seed` (usable to pin routing via `provider` preferences) |
| Provider routing | Single provider endpoint (`Seed`); no alternative routing variants exposed. |
| `resolution` | enum: `1K`, `2K`, `4K` |
| `aspect_ratio` | enum (18 values): `1:1`, `1:2`, `2:1`, `2:3`, `3:2`, `3:4`, `4:3`, `4:5`, `5:4`, `9:16`, `16:9`, `9:19.5`, `19.5:9`, `9:20`, `20:9`, `9:21`, `21:9`, `auto` |
| `n` | range 1–10 |
| `input_references` | range 0–14 (image-to-image / reference-image capable) |
| `seed` | boolean (supported) |
| `allowed_passthrough_parameters` | `[]` — **no** provider-specific passthrough keys accepted |
| Streaming | **Not supported** (`supports_streaming:false`) |
| Pricing | One record: `{ "billable": "output_image", "unit": "image", "cost_usd": 0.04 }` |
| Billing unit | per output **image** (flat, resolution-independent) |
| Available resolutions | `1K`, `2K`, `4K` (cost is the same flat $0.04/image regardless of resolution) |
| Image-to-image / reference images | **Supported** via `input_references` (0–14 images, HTTPS URLs or base64 data URLs) |
| Output modalities | `image` only |
| Input modalities | `image`, `text` |
| Limitations | No streaming; no passthrough parameters; no `quality`/`output_format`/`background`/`output_compression` knobs (those parameters are absent → unsupported by this endpoint). Resolution does not change cost (flat per-image). |

**Maximum cost for a single minimal paid request:** `1 image × $0.04 = $0.04` (flat, even at `4K`). If `n>1`, cost scales linearly (`n × $0.04`).

---

## Credential Reuse Recommendation

The goal is **one secure source of truth** for the OpenRouter key, shared by OpenCord, standalone test scripts, future ComfyUI API-backed nodes/workflows, and the ComfyUI MCP control plane (only if it directly requires OpenRouter access).

### Recommended architecture

```text
                 /home/naman/Documents/gen-video/.env
                 (single source of truth; standard dotenv)
                 OPENROUTER_API_KEY=sk-...
                              |
           any standard dotenv loader or process launcher
           exports OPENROUTER_API_KEY into the process env
                              |
        +---------------------+---------------------+---------------------+
        |                     |                     |                     |
   Test script          OpenCord            ComfyUI process       MCP control plane
  (reads env / .env)   (reads env / .env)  (env exported by      (only if it needs OR;
                                              launcher)            env exported by launcher)
        |                     |                     |                     |
        +---------------------+---------------------+---------------------+
                              |
                  Authorization: Bearer <key>   (header only, never logged)
```

### Rules

1. **Single source of truth:** `/home/naman/Documents/gen-video/.env` contains `OPENROUTER_API_KEY=<value>` in standard dotenv format. **Do not** copy the key into any other `.env`, config, JSON, prompt, log, or committed file.
2. **Standard interface:** every consumer reads `OPENROUTER_API_KEY` from its process environment. Any standard dotenv loader (python-dotenv, docker --env-file, direnv, systemd EnvironmentFile, shell `source`) can load the `.env`. No custom parser, no custom label convention.
3. **Consumers read the env var; they never hardcode the key.** The test script checks `OPENROUTER_API_KEY` in the process env first, then falls back to a standard `KEY=VALUE` parse of the `.env` file.
4. **ComfyUI:** launch the ComfyUI process with `OPENROUTER_API_KEY` exported (e.g. via a launcher that sources the `.env`). API-backed nodes read the env var internally. **Never** embed the key in workflow JSON or node parameters.
5. **ComfyUI MCP control plane:** only if it directly calls OpenRouter, read `OPENROUTER_API_KEY` from its process env. **Never** pass the key as an MCP tool argument or in prompts.
6. **No duplication:** the key is never written into `visual-agent-test/`, workflow JSON, MCP args, prompts, `REPORT.md`, or any committed file. Only the `.env` holds the value.
7. **Logging discipline:** Authorization headers are redacted (`Bearer ***REDACTED***`) in all metadata files. The test script's `sanitize()` helper strips any field whose name looks like a secret, `strip_b64()` removes image bytes, and `summarize_input_refs()` replaces data URLs with summaries before persistence.

---

## Prepared Test Script

| Item | Value |
| --- | --- |
| Script path | `visual-agent-test/scripts/test_openrouter_image_api.py` |
| Dependencies | **Python 3 standard library only** (`argparse`, `base64`, `json`, `mimetypes`, `os`, `urllib.request`, `urllib.error`, `pathlib`, `datetime`). No `requests`/`httpx`/`pip` required (none are installed in this environment). |
| Credential load | `OPENROUTER_API_KEY` env var first; falls back to standard dotenv parse of `.env`. Key is placed only in the `Authorization` header and is never printed/logged/stored. |
| Generation endpoint | `POST https://openrouter.ai/api/v1/images` (dedicated Image API) |
| Required args | `--model`, `--prompt` |
| Optional args | `--resolution`, `--aspect-ratio`, `--output-format`, `--seed`, `--n`, `--input-reference` (repeatable) |
| Image-to-image | `--input-reference` accepts HTTP(S) URLs (used directly) or local file paths (read, base64-encoded as `data:<mime>;base64,...`). Repeatable for multiple references. Nonexistent files fail with exit code 2. |
| Safety flag | `--dry-run` (validate args + credential + reference files; print target endpoint + sanitized payload; **no API call**) |
| Failure handling | Clear messages for HTTP errors (status + body captured), network errors, malformed JSON, missing `data`/`b64_json`, base64 decode errors, missing reference files. |
| Image decoding | Decodes `b64_json` per `data[i]`; detects `media_type`; maps to extension (`image/png→.png`, `image/jpeg→.jpg`, `image/webp→.webp`, `image/svg+xml→.svg`, fallback derived from media type). |
| Output location | Images → `visual-agent-test/outputs/<timestamp>_<model>_<i>.<ext>` |
| Metadata logging | `visual-agent-test/logs/<...>_response_meta.json` and `<...>_summary.json`. **b64_json is stripped** from all metadata (image bytes live only in the artifact file). `input_references` data URLs are summarized to `{source, media_type, base64_chars}` — no base64 in metadata. Authorization header redacted. |
| Usage/cost logging | Captures `usage` (`prompt_tokens`, `completion_tokens`, `total_tokens`) and `cost` (USD) from the response into both metadata files. |
| Auto-run protection | `--model`/`--prompt` are required; with no args argparse exits with code 2 and prints usage. The script never generates without explicit, complete arguments and no `--dry-run`. |

### Usage

```bash
# Text-to-image dry run (no cost)
python3 visual-agent-test/scripts/test_openrouter_image_api.py \
    --model bytedance-seed/seedream-4.5 \
    --prompt "a red panda astronaut, studio lighting" \
    --resolution 2K --aspect-ratio 1:1 --seed 42 --dry-run

# Image-to-image dry run (local reference file)
python3 visual-agent-test/scripts/test_openrouter_image_api.py \
    --model bytedance-seed/seedream-4.5 \
    --prompt "transform this product photo into a watercolor painting" \
    --input-reference /path/to/product.jpg \
    --resolution 2K --aspect-ratio 1:1 --dry-run

# Real paid image-to-image generation (run only after explicit approval)
python3 visual-agent-test/scripts/test_openrouter_image_api.py \
    --model bytedance-seed/seedream-4.5 \
    --prompt "transform this product photo into a watercolor painting" \
    --input-reference /path/to/product.jpg \
    --resolution 2K --aspect-ratio 1:1
```

### Dry-run results (executed during this task)

**Dry run 1 — text-to-image (credential loaded from standard dotenv):**

```
target endpoint : https://openrouter.ai/api/v1/images
models endpoint : https://openrouter.ai/api/v1/images/models
credential src  : standard dotenv (/home/naman/Documents/gen-video/.env)
credential ok   : yes (sk-*** redacted)
output dir      : /home/naman/Documents/gen-video/visual-agent-test/outputs
sanitized payload (b64_json and data URLs excluded):
{
  "model": "bytedance-seed/seedream-4.5",
  "prompt": "a red panda astronaut floating in space, studio lighting",
  "resolution": "2K",
  "aspect_ratio": "1:1",
  "seed": 42
}
DRY RUN: no generation request sent. No cost incurred.
```

**Dry run 2 — image-to-image (HTTP URL + local file references):**

```
sanitized payload (b64_json and data URLs excluded):
{
  "model": "bytedance-seed/seedream-4.5",
  "prompt": "transform this product photo into a watercolor painting style",
  "resolution": "2K",
  "aspect_ratio": "1:1",
  "input_references": [
    {
      "type": "image_url",
      "source": "http_url",
      "url": "https://example.com/product-photo.jpg"
    },
    {
      "type": "image_url",
      "source": "local_file",
      "media_type": "image/png",
      "base64_chars": 92
    }
  ]
}
DRY RUN: no generation request sent. No cost incurred.
```

**Dry run 3 — nonexistent reference file (error handling):**

```
ERROR: input reference file not found: /tmp/nonexistent_image.jpg
```
(exit code 2)

All three dry-runs: exit code 0 or 2 as expected. No request was sent; `outputs/` remains empty; only the two sanitized discovery logs exist under `logs/`. No API key leaked into any file.

---

## First Paid Test Recommendation

> **Not executed.** Awaiting explicit user approval.

The first paid test should be an **image-to-image (reference-image) editing call**, not a text-to-image generation. This tests the actual primitive the system needs: input asset → API call → transformed asset → artifact persistence → metadata → ComfyUI ingestion. A text-to-image astronaut only proves HTTP connectivity; an image-editing call proves the real workflow.

### Test specification

| Field | Value |
| --- | --- |
| Model | `bytedance-seed/seedream-4.5` |
| Operation | Image-to-image (reference-image editing) |
| Input | One real product photograph (e.g. a kitchenware product shot) provided as `--input-reference` |
| Prompt | A transformation prompt relevant to the business domain (e.g. "place this product on a marble countertop with soft natural lighting, editorial product photography style") |
| Resolution | `2K` |
| Aspect ratio | `1:1` |
| `n` | `1` (single output image) |
| `seed` | omitted (or a fixed value for reproducibility) |
| Expected maximum cost | **$0.04** (flat per output image; resolution-independent; failed/cancelled generations are not billed) |
| Output location | `visual-agent-test/outputs/<timestamp>_bytedance-seed_seedream-4.5_0.png` (media_type-driven extension) |
| Evidence to collect | `logs/<...>_response_meta.json` (sanitized request summary + response shape with b64_json stripped + usage), `logs/<...>_summary.json` (saved artifact path, bytes, media_type, `usage.cost`), and the decoded image file itself. |
| Success criteria | HTTP 200; `data[0].b64_json` decodes to a valid image; `media_type` reported; `usage.cost == 0.04`; artifact file is non-empty and opens as a valid image; the output visually reflects the reference image transformed by the prompt. |
| Metadata verification | Confirm `response_meta.json` does **not** contain `b64_json` anywhere; confirm `input_references` in metadata shows `{source:"local_file", media_type:"image/...", base64_chars:N}` — no raw base64 data. |

### Command to run after approval

```bash
python3 visual-agent-test/scripts/test_openrouter_image_api.py \
    --model bytedance-seed/seedream-4.5 \
    --prompt "place this product on a marble countertop with soft natural lighting, editorial product photography style" \
    --input-reference /path/to/real-product-photo.jpg \
    --resolution 2K --aspect-ratio 1:1
```

(Replace `/path/to/real-product-photo.jpg` with an actual product image from the business domain.)

---

# Integration Readiness Matrix

| Requirement | Status | Evidence / Notes |
| --- | --- | --- |
| OpenRouter Image Models API reachable | **READY** | `GET /api/v1/images/models` → HTTP 200 (39 models). Also verified per-endpoint endpoint. |
| Existing OpenRouter credential reusable | **READY** | `.env` converted to standard dotenv (`OPENROUTER_API_KEY=...`); dry-run confirms `credential ok: yes` from standard dotenv. Key never exposed. |
| Seedream 4.5 endpoint discoverable | **READY** | `GET /api/v1/images/models/bytedance-seed/seedream-4.5/endpoints` → HTTP 200; saved to `logs/seedream-4.5-endpoints.json`. |
| Endpoint pricing verified dynamically | **READY** | Endpoint record returns `pricing:[{billable:"output_image", unit:"image", cost_usd:0.04}]`; confirmed at runtime, not hardcoded from docs. |
| Standalone Image API client prepared | **READY** | `scripts/test_openrouter_image_api.py`; stdlib-only; text-to-image + image-to-image (`--input-reference`); `--dry-run` works (exit 0). |
| base64 response decoding implemented | **READY** | `base64.b64decode` per `data[i].b64_json`; decode errors handled. (Will execute on first paid run; code path verified by dry-run.) |
| b64_json stripped from metadata | **READY** | `strip_b64()` recursively removes `b64_json` from persisted response metadata. `summarize_input_refs()` replaces data URLs with summaries. Image bytes live only in artifact files. |
| Artifact persistence implemented | **READY** | Writes decoded bytes to `outputs/<timestamp>_<model>_<i>.<ext>`; extension chosen from `media_type`. (Outputs dir empty pending paid run.) |
| Usage/cost logging implemented | **READY** | Captures `usage` + `cost` into `logs/<...>_summary.json` and `<...>_response_meta.json`; headers redacted; no b64_json in metadata. |
| Image-to-image (`input_references`) supported | **READY** | `--input-reference` accepts HTTP URLs and local files (auto-encoded as data URLs); repeatable; dry-run verified both paths + error handling for missing files. |
| Dry-run successful | **READY** | Three dry-runs (text-to-image, image-to-image, error case) all pass; no API call; `outputs/` empty. |
| Paid generation test awaiting approval | **BLOCKED** (on user approval) | First paid test specified: image-to-image with Seedream 4.5, 2K, 1:1, one real product reference image, max $0.04. Not executed — by design. |

**Legend:** READY · PARTIALLY READY · MISSING · BLOCKED · UNVERIFIED

---

## Verified Path Summary

The established and verified path (stopping before the paid step):

```text
OpenRouter Credential (.env, standard dotenv: OPENROUTER_API_KEY=...)
            |
            v
Image Model Discovery  (GET /api/v1/images/models)        -- READY, 200 OK
            |
            v
Capability + Pricing Inspection  (GET .../endpoints)       -- READY, $0.04/image
            |
            v
Standalone API Client  (scripts/test_openrouter_image_api.py)  -- READY
  - text-to-image  -- dry-run OK
  - image-to-image (--input-reference, URL + local file)  -- dry-run OK
  - b64_json stripped from metadata  -- implemented
            |
            v
Dry Run  (--dry-run)                                       -- READY, exit 0
            |
            v
User Approval                                              -- BLOCKED (awaiting)
            |
            v
One Paid Image-Editing Generation
  (Seedream 4.5, 2K, 1:1, n=1, real product reference)    -- PENDING APPROVAL
            |
            v
Decoded Local Artifact  (outputs/*.png)                    -- READY to receive
            |
            v
Sanitized Metadata  (no b64_json, no key, no data URLs)    -- READY to receive
            |
            v
ComfyUI Integration                                        -- UNVERIFIED (next phase)
            |
            v
OpenCord-Controlled Workflow                               -- UNVERIFIED (next phase)
```

**Stopping here as instructed.** No paid image-generation call has been made. Awaiting user approval to execute the single First Paid Test (image-to-image with a real product reference) described above.
