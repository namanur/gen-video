# End-to-End Pipeline Contract

## Phase A: topic to evidence

1. Research director writes research questions and the research brief.
2. Source, book, and data researchers investigate complementary evidence lanes.
3. Every source is catalogued in SQLite. Locally useful PDFs, screenshots, article snapshots, charts, and recordings are saved and registered at discovery time.
4. Analysts produce an evidence matrix where every claim has an ID, source IDs, a locator, evidence strength, and limitations.
5. Counterevidence and fact-check agents audit claims before the narrative is written.

Agents must search the existing SQLite catalog by title, tags, description, origin URL, and hash before making another capture.

## Phase B: evidence to spoken script

The script writer receives the brief and evidence matrix, not an unstructured dump. Expected outputs are outline, spoken script, claim ledger, and recording notes with pronunciation, numbers, optional emphasis, and visual cues.

The prose should sound like a person who has read the research and is explaining it to another person. Do not force punchlines, symmetrical lists, or a contrarian take the evidence does not warrant.

## Phase C: narration and timing

The creator records their own voice, or text is sent to Google Cloud Text-to-Speech. Retain a versioned copy of the exact script used for the audio.

For creator-recorded narration, run speech recognition plus forced alignment or another timestamp-capable workflow, then manually spot-check proper nouns, pauses, and sentence boundaries. For generated narration, Google TTS accepts text or SSML; preserve the exact input and voice/audio configuration alongside the output.

Align against the **final audio file**. If the audio changes, the timestamps are stale and must be regenerated.

The importer accepts provider-normalized JSON with segments like this:

    {
      "language": "en",
      "audio_path": "narration/final/voice-v001.wav",
      "segments": [
        {
          "id": "seg-0001",
          "start": 0.0,
          "end": 4.81,
          "text": "Let us start with what the evidence actually says.",
          "words": [
            {"text": "Let", "start": 0.0, "end": 0.18},
            {"text": "us", "start": 0.19, "end": 0.31}
          ]
        }
      ]
    }

Word-level alignment is useful but can be wrong. Do not assume every provider has the same schema. Put a small provider-specific adapter before this normalized format rather than making the rest of the harness provider-dependent.

## Phase D: assets to scene plan

The scene planner uses the timed transcript as the primary clock and the database as the source of truth for visuals.
Each scene states:
- absolute start/end in seconds
- transcript segment IDs
- what the viewer should see and why
- asset IDs, or an explicit generated-graphic specification
- on-screen text and animation notes
- citation/attribution requirement
- confidence and unresolved asset requests

Prefer reusing an approved catalog asset over a new browser round-trip. An asset description must say what is visibly present, not only repeat the source title. A new image or screen recording must be saved and registered before a render-ready plan references it.

## Phase E: validate, preview, render

1. Validate scene timing and every referenced asset.
2. Render a short preview first, for example 20-30 seconds at a smaller frame size.
3. Review chronology, visual continuity, text clipping, citation overlays, and narration sync.
4. Render the full video with Hyperframes once the preview is sound.
5. Save the render command, tool/model versions, scene plan, and output hash in episode logs.

A ten-minute video at 30 fps contains 18,000 frames. On an older i3 laptop, budget for a slow full render and make short previews the default. Do not use the full render as the first debugging tool.

## Phase F: review and learning loop

Store review decisions as edits to the plan or asset review state, not only in chat. If an image is rejected, mark it rejected and record the reason; do not silently delete provenance. If a new screenshot is needed, register it and link it to the relevant source/claim where possible.

## Adapter boundaries

- Search/browser adapter: source discovery, page navigation, screenshot/HTML capture, PDF download.
- LLM adapter: role-specific agent calls and constrained structured outputs.
- TTS adapter: Google Cloud TTS using text/SSML.
- Transcription/alignment adapter: provider-specific output converted to normalized timed transcript.
- Asset catalog: SQLite database and local files.
- Render adapter: scene plan converted to Hyperframes HTML composition and rendered to MP4.

Keep API keys in environment variables, never in prompts, episode JSON, screenshots, source files, or logs.
