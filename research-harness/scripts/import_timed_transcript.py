#!/usr/bin/env python3
"""Import provider-normalized timed narration JSON into an episode catalog."""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import re
import shutil
import sqlite3
import subprocess
import wave
from datetime import datetime, timezone
from pathlib import Path

TIMING_TOLERANCE = 0.05
TRANSCRIPT_ID_PATTERN = re.compile(r"^[A-Za-z0-9._-]+$")


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def duration_tolerance(duration: float) -> float:
    """Allow small container/reporting differences without hiding stale audio."""
    return max(0.05, min(0.5, duration * 0.001))


def actual_audio_duration(path: Path) -> float | None:
    """Read WAV duration directly; use ffprobe for other containers when available."""
    if path.suffix.lower() == ".wav":
        try:
            with wave.open(str(path), "rb") as handle:
                rate = handle.getframerate()
                if rate > 0:
                    return handle.getnframes() / float(rate)
        except (wave.Error, OSError):
            return None
    ffprobe = shutil.which("ffprobe")
    if not ffprobe:
        return None
    try:
        result = subprocess.run(
            [ffprobe, "-v", "error", "-show_entries", "format=duration",
             "-of", "default=noprint_wrappers=1:nokey=1", str(path)],
            capture_output=True, text=True, timeout=20, check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if result.returncode != 0:
        return None
    try:
        return float(result.stdout.strip())
    except ValueError:
        return None


def validate_payload(payload: object) -> list[dict]:
    """Validate the provider-neutral transcript contract before touching the catalog."""
    if not isinstance(payload, dict):
        raise ValueError("input must be an object with a segments array")
    audio_path = payload.get("audio_path")
    if not isinstance(audio_path, str) or not audio_path.strip():
        raise ValueError("audio_path is required")
    try:
        duration = float(payload["duration_seconds"])
    except (KeyError, TypeError, ValueError) as error:
        raise ValueError("duration_seconds must be a positive number") from error
    if duration <= 0:
        raise ValueError("duration_seconds must be a positive number")
    if not math.isfinite(duration):
        raise ValueError("duration_seconds must be finite")
    segments = payload.get("segments")
    if not isinstance(segments, list) or not segments:
        raise ValueError("input must contain at least one transcript segment")

    seen_ids: set[str] = set()
    previous_start = -1.0
    for index, segment in enumerate(segments):
        if not isinstance(segment, dict):
            raise ValueError(f"segment {index} must be an object")
        segment_id = str(segment.get("id") or "").strip()
        if not segment_id:
            raise ValueError(f"segment {index} needs a stable id")
        if segment_id in seen_ids:
            raise ValueError(f"duplicate segment id: {segment_id}")
        seen_ids.add(segment_id)
        if not isinstance(segment.get("text"), str) or not segment["text"].strip():
            raise ValueError(f"segment {segment_id} needs non-empty text")
        try:
            start = float(segment["start"])
            end = float(segment["end"])
        except (KeyError, TypeError, ValueError) as error:
            raise ValueError(f"segment {segment_id} needs numeric start and end") from error
        if start < 0 or end <= start:
            raise ValueError(f"segment {segment_id} has invalid time range {start}..{end}")
        if not math.isfinite(start) or not math.isfinite(end):
            raise ValueError(f"segment {segment_id} has non-finite timings")
        if start < previous_start:
            raise ValueError(f"segments are not sorted by start time at {segment_id}")
        if end > duration + TIMING_TOLERANCE:
            raise ValueError(
                f"segment {segment_id} ends at {end}s, beyond the declared duration {duration}s"
            )
        words = segment.get("words", [])
        if not isinstance(words, list):
            raise ValueError(f"segment {segment_id} words must be an array")
        for word in words:
            if not isinstance(word, dict) or not word.get("text"):
                raise ValueError(f"segment {segment_id} contains a malformed word")
            try:
                word_start, word_end = float(word["start"]), float(word["end"])
            except (KeyError, TypeError, ValueError) as error:
                raise ValueError(f"segment {segment_id} has a word without numeric timings") from error
            if word_start < start - TIMING_TOLERANCE or word_end > end + TIMING_TOLERANCE:
                raise ValueError(f"word timing is outside segment bounds: {word}")
            if word_end <= word_start or not math.isfinite(word_start) or not math.isfinite(word_end):
                raise ValueError(f"word timing is invalid: {word}")
        previous_start = start
    return segments


def resolve_audio(root: Path, audio_path: str) -> tuple[Path, str]:
    candidate = Path(audio_path).expanduser()
    if not candidate.is_absolute():
        candidate = root / candidate
    candidate = candidate.resolve()
    try:
        relative = candidate.relative_to(root).as_posix()
    except ValueError as error:
        raise ValueError("audio_path must stay inside the episode root") from error
    if not candidate.is_file():
        raise ValueError(f"audio file does not exist: {relative}")
    return candidate, relative


def relative_source_path(root: Path, source_path: str | None) -> str | None:
    if not source_path:
        return None
    try:
        return Path(source_path).expanduser().resolve().relative_to(root).as_posix()
    except (ValueError, OSError):
        return None


def ingest_transcript(root: str | Path, payload: dict, transcript_id: str = "T-001",
                      replace: bool = False, source_path: str | None = None) -> dict:
    """Validate, fingerprint, and persist one transcript version."""
    root = Path(root).expanduser().resolve()
    db_path = root / "episode.sqlite3"
    if not db_path.is_file():
        raise FileNotFoundError(f"episode database not found: {db_path}")
    transcript_id = transcript_id.strip()
    if not TRANSCRIPT_ID_PATTERN.fullmatch(transcript_id):
        raise ValueError("transcript_id may contain only letters, numbers, dots, dashes, and underscores")

    segments = validate_payload(payload)
    audio_file, audio_relative = resolve_audio(root, str(payload["audio_path"]))
    digest = sha256_file(audio_file)
    declared_hash = payload.get("audio_sha256")
    if declared_hash is not None:
        if not isinstance(declared_hash, str) or declared_hash.lower() != digest:
            raise ValueError(
                "audio_sha256 does not match the audio file; timestamps belong to different audio"
            )

    declared_duration = float(payload["duration_seconds"])
    measured_duration = actual_audio_duration(audio_file)
    duration_verified = measured_duration is not None
    if audio_file.suffix.lower() == ".wav" and measured_duration is None:
        raise ValueError("could not read the WAV duration; the audio file may be corrupt")
    if measured_duration is not None and abs(measured_duration - declared_duration) > duration_tolerance(measured_duration):
        raise ValueError(
            f"declared duration {declared_duration}s does not match measured audio duration "
            f"{measured_duration:.3f}s"
        )

    try:
        schema_version = int(payload.get("schema_version", 1))
    except (TypeError, ValueError) as error:
        raise ValueError("schema_version must be an integer") from error
    language = payload.get("language", "en")
    if not isinstance(language, str) or not language.strip():
        raise ValueError("language must be a non-empty string")
    alignment_method = payload.get("alignment_method")
    if alignment_method is not None and not isinstance(alignment_method, str):
        raise ValueError("alignment_method must be a string when present")

    now = datetime.now(timezone.utc).isoformat()
    with sqlite3.connect(db_path) as connection:
        connection.execute("PRAGMA foreign_keys = ON")
        has_table = connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='transcripts'"
        ).fetchone()
        if not has_table:
            raise ValueError(
                "episode database predates transcript versioning; recreate it with init_episode.py"
            )
        existing = connection.execute(
            "SELECT 1 FROM transcripts WHERE transcript_id = ?", (transcript_id,)
        ).fetchone()
        if existing and not replace:
            raise ValueError(
                f"transcript {transcript_id} already exists; use --replace to replace that version"
            )
        if existing:
            connection.execute(
                "UPDATE scenes SET status = 'stale' WHERE transcript_id = ?", (transcript_id,)
            )
            connection.execute("DELETE FROM transcripts WHERE transcript_id = ?", (transcript_id,))
        connection.execute(
            "INSERT INTO transcripts(transcript_id, audio_path, audio_sha256, duration_seconds, "
            "language, alignment_method, schema_version, source_path, imported_at) "
            "VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (transcript_id, audio_relative, digest, declared_duration, language.strip(),
             alignment_method, schema_version, relative_source_path(root, source_path), now)
        )
        for segment in segments:
            connection.execute(
                "INSERT INTO transcript_segments "
                "(segment_id, transcript_id, start_seconds, end_seconds, text, words_json, "
                "speaker, confidence, created_at) VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (str(segment["id"]), transcript_id, float(segment["start"]), float(segment["end"]),
                 segment["text"].strip(), json.dumps(segment.get("words", []), ensure_ascii=False),
                 segment.get("speaker"), segment.get("confidence"), now)
            )
        connection.execute(
            "INSERT OR REPLACE INTO project_meta(key, value) VALUES('current_transcript_id', ?)",
            (transcript_id,)
        )
        connection.execute(
            "INSERT INTO activity_log(created_at, actor, action, entity_type, entity_id, details_json) "
            "VALUES(?, ?, ?, ?, ?, ?)",
            (now, "import_timed_transcript.py", "transcript_imported", "transcript", transcript_id,
             json.dumps({"audio_path": audio_relative, "audio_sha256": digest,
                         "duration_seconds": declared_duration, "segments": len(segments),
                         "replace": replace}, ensure_ascii=False))
        )

    normalized = dict(payload)
    normalized.update({
        "transcript_id": transcript_id,
        "audio_path": audio_relative,
        "audio_sha256": digest,
        "duration_seconds": declared_duration,
        "schema_version": schema_version,
        "language": language.strip(),
        "segments": segments,
    })
    canonical = root / "transcript" / "timed_transcript.json"
    versioned = root / "transcript" / "versions" / f"{transcript_id}.json"
    canonical.parent.mkdir(parents=True, exist_ok=True)
    versioned.parent.mkdir(parents=True, exist_ok=True)
    serialized = json.dumps(normalized, indent=2, ensure_ascii=False) + chr(10)
    canonical.write_text(serialized, encoding="utf-8")
    if source_path is None or Path(source_path).resolve() != versioned.resolve():
        versioned.write_text(serialized, encoding="utf-8")

    return {
        "transcript_id": transcript_id,
        "segments": len(segments),
        "audio_path": audio_relative,
        "audio_sha256": digest,
        "duration_seconds": declared_duration,
        "measured_duration": measured_duration,
        "duration_verified": duration_verified,
        "canonical_path": canonical,
        "versioned_path": versioned,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", required=True)
    parser.add_argument("--input", required=True)
    parser.add_argument("--transcript-id", default="T-001",
                        help="Stable version ID, for example T-001")
    parser.add_argument("--replace", action="store_true",
                        help="Replace this transcript version and its segments")
    args = parser.parse_args()

    root = Path(args.root).expanduser().resolve()
    input_path = Path(args.input).expanduser().resolve()
    if not (root / "episode.sqlite3").is_file():
        parser.error(f"episode database not found: {root / 'episode.sqlite3'}")
    if not input_path.is_file():
        parser.error(f"transcript JSON not found: {input_path}")
    try:
        payload = json.loads(input_path.read_text(encoding="utf-8"))
        result = ingest_transcript(
            root, payload, transcript_id=args.transcript_id, replace=args.replace,
            source_path=str(input_path),
        )
    except (json.JSONDecodeError, ValueError, FileNotFoundError, OSError, sqlite3.Error) as error:
        parser.error(f"invalid timed transcript: {error}")

    print(f"Imported {result['segments']} timed segments as {result['transcript_id']}")
    print(f"Audio: {result['audio_path']} ({result['duration_seconds']}s)")
    print(f"SHA-256: {result['audio_sha256']}")
    if result["duration_verified"]:
        print(f"Measured duration: {result['measured_duration']:.3f}s")
    else:
        print("Duration check: not verified for this container; declared duration was used.")
    print(f"Canonical transcript: {result['canonical_path']}")
    print(f"Versioned transcript: {result['versioned_path']}")
    print("Audio path and hash now identify the exact narration used for these timestamps.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
