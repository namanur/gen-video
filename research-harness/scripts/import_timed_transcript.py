#!/usr/bin/env python3
"""Import provider-normalized timed narration JSON into an episode catalog."""
from __future__ import annotations

import argparse
import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path


def validate_payload(payload: object) -> list[dict]:
    if not isinstance(payload, dict) or not isinstance(payload.get("segments"), list):
        raise ValueError("input must be an object with a segments array")
    segments = payload["segments"]
    previous_start = -1.0
    for index, segment in enumerate(segments):
        if not isinstance(segment, dict):
            raise ValueError(f"segment {index} must be an object")
        if not segment.get("id") or not isinstance(segment.get("text"), str) or not segment["text"].strip():
            raise ValueError(f"segment {index} needs id and non-empty text")
        start = float(segment["start"])
        end = float(segment["end"])
        if start < 0 or end <= start:
            raise ValueError(f"segment {segment['id']} has invalid time range {start}..{end}")
        if start < previous_start:
            raise ValueError(f"segments are not sorted by start time at {segment['id']}")
        words = segment.get("words", [])
        if not isinstance(words, list):
            raise ValueError(f"segment {segment['id']} words must be an array")
        for word in words:
            if not isinstance(word, dict) or not word.get("text"):
                raise ValueError(f"segment {segment['id']} contains a malformed word")
            word_start, word_end = float(word["start"]), float(word["end"])
            if word_start < start - 0.05 or word_end > end + 0.05 or word_end <= word_start:
                raise ValueError(f"word timing is outside segment bounds: {word}")
        previous_start = start
    return segments


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", required=True)
    parser.add_argument("--input", required=True)
    parser.add_argument("--replace", action="store_true", help="Delete old transcript segments first")
    args = parser.parse_args()

    root = Path(args.root).expanduser().resolve()
    db_path = root / "episode.sqlite3"
    input_path = Path(args.input).expanduser().resolve()
    if not db_path.is_file():
        parser.error(f"episode database not found: {db_path}")
    if not input_path.is_file():
        parser.error(f"transcript JSON not found: {input_path}")
    try:
        payload = json.loads(input_path.read_text(encoding="utf-8"))
        segments = validate_payload(payload)
    except (json.JSONDecodeError, ValueError, KeyError, TypeError) as error:
        parser.error(f"invalid timed transcript: {error}")

    now = datetime.now(timezone.utc).isoformat()
    with sqlite3.connect(db_path) as connection:
        connection.execute("PRAGMA foreign_keys = ON")
        if args.replace:
            connection.execute("DELETE FROM transcript_segments")
        for segment in segments:
            connection.execute(
                "INSERT OR REPLACE INTO transcript_segments "
                "(segment_id, start_seconds, end_seconds, text, words_json, speaker, confidence, created_at) "
                "VALUES(?, ?, ?, ?, ?, ?, ?, ?)",
                (str(segment["id"]), float(segment["start"]), float(segment["end"]),
                 segment["text"].strip(), json.dumps(segment.get("words", []), ensure_ascii=False),
                 segment.get("speaker"), segment.get("confidence"), now)
            )
        connection.execute(
            "INSERT INTO activity_log(created_at, actor, action, entity_type, entity_id, details_json) "
            "VALUES(?, ?, ?, ?, ?, ?)",
            (now, "import_timed_transcript.py", "transcript_imported", "transcript", str(len(segments)),
             json.dumps({"source_file": str(input_path), "replace": args.replace}, ensure_ascii=False))
        )

    destination = root / "transcript" / "timed_transcript.json"
    destination.parent.mkdir(parents=True, exist_ok=True)
    if input_path != destination:
        destination.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + chr(10), encoding="utf-8")
    print(f"Imported {len(segments)} timed segments into {db_path}")
    print(f"Canonical transcript: {destination}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
