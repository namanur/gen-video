# Project status: research-to-video harness

Last updated: 2026-10-26  
Branch: `feat/research-asset-video-harness`  
Master specification: `research-harness/MASTER_WORKFLOW.md` (41,365 bytes, 834 lines)

## Handoff finding

The handoff document was verified against the repository. It is a real, substantial production specification, not a finished system. The repository contains the local contracts and validation needed to start the pilot, but it does not yet contain browser capture, multi-agent orchestration, narration generation, asset-aware scene planning, or Hyperframes rendering.

The most important gap is not missing scaffolding. It is that no pilot episode has been produced yet. The next work should use the existing contracts to produce one short evidence-led sequence before expanding the framework.

## Implemented

- Episode initialization with expanded evidence, asset, narration, transcript, edit, export, and log folders.
- SQLite catalog for sources, claims, citations, assets, transcripts, transcript segments, scene plans, scenes, scene assets, scene citations, and scene transcript links.
- Idempotent schema upgrades through `scripts/db_contracts.py`, including archival of pre-versioning transcript segments.
- Source registration with canonical URL, bibliographic fields, local copy, SHA-256, rights state, and quality notes.
- Asset registration with SHA-256, MIME type, tags, provenance, review state, rights state, review note, and reviewed timestamp.
- Catalog search over titles, URLs, descriptions, tags, paths, and asset hashes.
- Audio-versioned transcript import. Each transcript records the exact audio path, SHA-256, measured duration, language, and alignment method. Transcript IDs are generated deterministically when not supplied.
- Canonical transcript JSON at `transcript/{transcript_id}.json`.
- Scene-plan validation for plan identity, transcript identity, audio path and hash, narration duration, transcript segment coverage, citation source IDs, asset existence, asset review state, timing, and local file paths.
- Scene-plan persistence into `scene_plans`, `scenes`, `scene_assets`, `scene_citations`, and `scene_transcript_segments`.
- Offline contract tests and a GitHub Actions workflow.

## Test evidence

    python3 -m unittest discover -s research-harness/tests -v

The current suite passes 16 tests. It covers transcript validation, duplicate segment IDs, duration limits, legacy migration, audio binding, stale audio detection, unknown transcript segments, scene coverage, missing citations, empty segment lists, duplicate citations, asset approval, missing asset files, and persisted scene relationships.

## Not implemented

- Browser and search capture adapter.
- Claims and citations command-line workflow.
- Research director and bounded multi-agent research runs.
- Creator recording import, Google Cloud TTS, transcription, and forced alignment adapters.
- Asset-aware scene planner.
- Hyperframes composition and preview rendering.
- Asset review gallery and scene review interface.
- Dependency-aware reruns.

## Known gaps

- Claims, claim-source links, and citation locators have tables but no command-line workflow.
- Asset review decisions keep a note and timestamp, but there is no separate review-history table.
- The scene planner is still a manual or agent-authored JSON step.
- No render benchmark exists for the target laptop.
- The master workflow still describes some intended interfaces more broadly than the current implementation. The README and this status file describe the executable contract.

## Next smallest useful step

Produce a 60-second vertical slice with real material:

1. Pick one narrow question from the ragebait pilot.
2. Register two primary sources and one saved artifact.
3. Write and record 45 to 60 seconds of narration.
4. Import the audio and timed transcript.
5. Create one to three scenes with real asset IDs and citation IDs.
6. Validate and persist the plan.
7. Render a short preview once the Hyperframes adapter exists.

Only after that slice works should the full ten-minute pilot and the remaining agent roles be expanded.

## Reproduction commands

    python3 research-harness/scripts/init_episode.py \
      --root projects/ragebait-001 \
      --slug ragebait-001 \
      --title "Why Ragebait Works Even When You Know It Is Ragebait" \
      --topic "Psychology of outrage and online incentives"

    python3 research-harness/scripts/import_timed_transcript.py \
      --root projects/ragebait-001 \
      --input projects/ragebait-001/transcript/timed_transcript.json \
      --audio narration/final/voice-v001.wav \
      --alignment-method manual

    python3 research-harness/scripts/validate_scene_plan.py \
      --root projects/ragebait-001 \
      --plan projects/ragebait-001/edit/scene_plan.json

The validator persists a passing plan by default. Use `--no-persist` for a dry run and `--allow-candidate` only for private drafts.
