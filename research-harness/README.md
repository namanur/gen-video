# Research-to-Video Harness

An asset-aware research and video production workflow for the gen-video project. The first pilot is a 10-minute essay: **Why Ragebait Works Even When You Know It Is Ragebait**.

The rule: **a research action should produce reusable evidence and a registered asset, not just a chat answer**. Articles, PDFs, relevant page screenshots, book references, diagrams, and screen recordings should be saved once, indexed in SQLite, and available to later agents without repeating the research.

## What this first increment provides

- Episode-folder initializer and SQLite catalog with idempotent schema upgrades.
- Asset registration CLI with SHA-256 fingerprints, review and rights state, and a review note.
- Audio-versioned timed-transcript importer. Each transcript records the narration path, SHA-256, measured duration, language, and alignment method.
- Scene-plan validator that checks the plan, transcript, audio hash, transcript segments, citation sources, assets, local files, and timing, then persists the validated scene relationships.
- Multi-agent roles, handoff protocol, and Hyperframes timeline contract.
- Offline contract tests and a GitHub Actions workflow.

This is not yet an autonomous browser agent. The orchestration runtime (OpenCode or another agent harness) must call research/browser tools, save the results, and register each artifact using these contracts.

See [PROJECT_STATUS.md](PROJECT_STATUS.md) for the verified handoff finding, test evidence, gaps, and next steps.

## Quick start

From the repository root:

    python3 research-harness/scripts/init_episode.py \
      --root projects/ragebait-001 \
      --slug ragebait-001 \
      --title "Why Ragebait Works Even When You Know It Is Ragebait" \
      --topic "Psychology of outrage, online incentives, and attention"

Register an artifact after an agent downloads or captures it:

    python3 research-harness/scripts/register_asset.py \
      --root projects/ragebait-001 \
      --path sources/screenshots/SRC-001/experiment-results.png \
      --asset-type screenshot \
      --description "Screenshot of the study's results table" \
      --source-url "https://example.org/study" \
      --rights-status fair_use_review \
      --review-note "Clear table; publication rights still under review"

Import timestamps after the final narration is recorded or generated:

    python3 research-harness/scripts/import_timed_transcript.py \
      --root projects/ragebait-001 \
      --input projects/ragebait-001/transcript/timed_transcript.json \
      --audio narration/final/my-voice-v001.wav \
      --alignment-method manual \
      --replace

The importer measures WAV duration directly and uses `ffprobe` for other formats when available. For an audio format that cannot be probed, pass `--duration-seconds` from the trusted audio-generation/metadata step. The importer hashes the selected file, checks any supplied hash/path/duration metadata, and stores immutable transcript-version identity in SQLite. `--replace` replaces only the same transcript ID; it does not erase other versions.

Every scene plan must carry `plan_id`, `transcript_id`, and the matching audio path, SHA-256, and duration. Each scene's `transcript_segment_ids` must exist in that transcript and fit inside the scene time range. Every `citation_source_ids` entry must exist in the episode source catalog.

Validate and persist a scene plan before it is passed to Hyperframes:

    python3 research-harness/scripts/validate_scene_plan.py \
      --root projects/ragebait-001 \
      --plan projects/ragebait-001/edit/scene_plan.json

A successful scene-plan validation is persisted to SQLite, including scene-to-asset, scene-to-transcript-segment, and scene-to-source relationships. The validator refuses to persist plans whose audio hash, registered transcript, segment IDs, source IDs, asset approvals, or timings do not match.

The scripts use Python's standard library. For non-WAV audio, the importer optionally calls `ffprobe` when it is available; otherwise pass `--duration-seconds`. Browser capture, transcription/alignment, Google Cloud TTS, and Hyperframes are separate adapters; episode initialization never invokes models, network calls, or paid APIs. Existing timed segments that predate audio-version tracking are archived as unbound legacy data and are never accepted as current transcript evidence.

Run the offline contract tests:

    python3 -m unittest discover -s research-harness/tests -v

## Episode folder

Each episode owns its evidence and database.

    projects/ragebait-001/
      episode.json
      episode.sqlite3
      research/
      sources/
        pages/
        pdfs/
        screenshots/
        books/
        datasets/
        metadata/
      assets/
        images/
        diagrams/
        charts/
        video/
        screen-recordings/
        audio/
        thumbnails/
        contact-sheets/
      script/
        versions/
      narration/
        original/
        final/
        tts-input/
        tts-config/
      transcript/
        TR-EXAMPLE.json
        versions/
        provider-original/
      edit/
        scene_plan.json
        hyperframes/
        preview/
      exports/
        draft/
        final/
      logs/
        research_runs/
        agent_handoffs/
        render_runs/
        errors/

## Important distinction

The database separates source provenance, asset approval, and usage rights. Capturing a screenshot, saving a PDF, or citing a book does not grant permission to reproduce it in the final video.

The schema is upgraded in place by `scripts/db_contracts.py`. Pre-versioning transcript segments are preserved in `transcript_segments_legacy_unbound` and are never treated as aligned to current audio.

## DeepSeek model setting

DeepSeek's October 17, 2026 changelog says its current V4.1 Flash revision is V4.1-Flash-1017 and the API alias remains deepseek-flash. Keep the stable alias in orchestration configuration and log the configured model and run date rather than hard-coding a stale version. Official update: https://api-docs.deepseek.com/updates/

## References

- Hyperframes introduction: https://hyperframes.app/docs/1-startup/1-introduction
- Hyperframes HTML schema: https://hyperframes.app/docs/6-reference/html-schema
- Google Cloud Text-to-Speech: https://docs.cloud.google.com/text-to-speech/docs/create-audio
