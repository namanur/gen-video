# Research-to-Video Harness

An asset-aware research and video production workflow for the gen-video project. The first pilot is a 10-minute essay: **Why Ragebait Works Even When You Know It Is Ragebait**.

The rule: **a research action should produce reusable evidence and a registered asset, not just a chat answer**. Articles, PDFs, relevant page screenshots, book references, diagrams, and screen recordings should be saved once, indexed in SQLite, and available to later agents without repeating the research.

## What this first increment provides

- Episode-folder initializer and SQLite catalog.
- Asset registration CLI with SHA-256 fingerprints plus review and rights state.
- Canonical timed-transcript importer for creator-recorded narration or Google TTS output.
- Scene-plan validator to catch missing assets and timing errors before rendering.
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
      --replace

Validate a scene plan before it is passed to Hyperframes:

    python3 research-harness/scripts/validate_scene_plan.py \
      --root projects/ragebait-001 \
      --plan projects/ragebait-001/edit/scene_plan.json

The scripts use Python's standard library only. Browser capture, transcription/alignment, Google Cloud TTS, and Hyperframes are separate adapters; episode initialization never invokes models, network calls, or paid APIs.

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
      assets/
        images/
        diagrams/
        video/
        screen-recordings/
        audio/
      script/
      narration/
        original/
        final/
      transcript/
      edit/
        hyperframes/
      exports/
      logs/

## Important distinction

The database separates source provenance, asset approval, and usage rights. Capturing a screenshot, saving a PDF, or citing a book does not grant permission to reproduce it in the final video.

## DeepSeek model setting

DeepSeek's October 17, 2026 changelog says its current V4.1 Flash revision is V4.1-Flash-1017 and the API alias remains deepseek-flash. Keep the stable alias in orchestration configuration and log the configured model and run date rather than hard-coding a stale version. Official update: https://api-docs.deepseek.com/updates/

## References

- Hyperframes introduction: https://hyperframes.app/docs/1-startup/1-introduction
- Hyperframes HTML schema: https://hyperframes.app/docs/6-reference/html-schema
- Google Cloud Text-to-Speech: https://docs.cloud.google.com/text-to-speech/docs/create-audio
