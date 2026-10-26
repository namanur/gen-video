# Research-to-Video Harness

An asset-aware research and video production workflow for the gen-video project. The first pilot is a 10-minute essay: **Why Ragebait Works Even When You Know It Is Ragebait**.

The rule: **a research action should produce reusable evidence and a registered asset, not just a chat answer**. Articles, PDFs, relevant page screenshots, book references, diagrams, and screen recordings should be saved once, indexed in SQLite, and available to later agents without repeating the research.

## What this first increment provides

- Episode-folder initializer and SQLite catalog.
- Asset registration CLI with SHA-256 fingerprints plus review and rights state, including a review note.
- Audio-versioned timed-transcript importer. Each transcript records the narration path, SHA-256, measured duration, language, and alignment method.
- Scene-plan validator that checks the transcript version, audio hash, transcript segment IDs, citation source IDs, assets, local files, and review state.
- Optional scene persistence: validated scenes and their asset, segment, and source links are written to SQLite.
- Multi-agent roles, handoff protocol, and Hyperframes timeline contract.

This is not yet an autonomous browser agent. The orchestration runtime (OpenCode or another agent harness) must call research/browser tools, save the results, and register each artifact using these contracts.

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
      --rights-status fair_use_review

Import timestamps after the final narration is recorded or generated:

    python3 research-harness/scripts/import_timed_transcript.py \
      --root projects/ragebait-001 \
      --input projects/ragebait-001/transcript/timed_transcript.json \
      --transcript-id T-001 \
      --replace

The JSON must point to a real audio file inside the episode folder. The importer computes the file's SHA-256, checks the declared duration against the container, records a transcript version, and sets it as current. If the audio changes, import a new version instead of editing the old timestamps.

Validate a scene plan before it is passed to Hyperframes:

    python3 research-harness/scripts/validate_scene_plan.py \
      --root projects/ragebait-001 \
      --plan projects/ragebait-001/edit/scene_plan.json \
      --save

The plan's `audio` object must include `transcript_id`, `path`, `sha256`, and `duration_seconds`. Validation fails if the plan references a different audio file, an unknown transcript segment, an unknown citation source, a missing asset, or an unapproved asset. `--save` writes the validated scenes and their links to SQLite.

Run the offline contract tests:

    python3 -m unittest discover -s research-harness/tests -v

The scripts use Python's standard library. For non-WAV audio, the importer optionally calls `ffprobe` when it is available; otherwise it records the declared duration without a container-level check. Browser capture, transcription/alignment, Google Cloud TTS, and Hyperframes are separate adapters; episode initialization never invokes models, network calls, or paid APIs.

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
        timed_transcript.json
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

The catalog schema is version 2. Episode databases created with the earlier scaffold must be recreated; the repository does not ship a migration for pre-pilot data.

## DeepSeek model setting

DeepSeek's October 17, 2026 changelog says its current V4.1 Flash revision is V4.1-Flash-1017 and the API alias remains deepseek-flash. Keep the stable alias in orchestration configuration and log the configured model and run date rather than hard-coding a stale version. Official update: https://api-docs.deepseek.com/updates/

## References

- Hyperframes introduction: https://hyperframes.app/docs/1-startup/1-introduction
- Hyperframes HTML schema: https://hyperframes.app/docs/6-reference/html-schema
- Google Cloud Text-to-Speech: https://docs.cloud.google.com/text-to-speech/docs/create-audio
