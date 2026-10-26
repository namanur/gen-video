#!/usr/bin/env python3
"""Import timestamped narration tied to an exact, fingerprinted audio version."""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import shutil
import sqlite3
import subprocess
import wave
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _number(value: Any, label: str) -> float:
    if isinstance(value, bool):
        raise ValueError(f"{label} must be a number")
    try:
        number = float(value)
    except (TypeError, ValueError) as error:
        raise ValueError(f"{label} must be a number") from error
    if not math.isfinite(number):
        raise ValueError(f"{label} must be finite")
    return number


def validate_payload(payload: object, duration_seconds: float | None = None) -> list[dict]:
    if not isinstance(payload, dict) or not isinstance(payload.get("segments"), list):
        raise ValueError("input must be an object with a segments array")
    segments = payload["segments"]
    previous_start = -1.0
    seen_ids: set[str] = set()
    for index, segment in enumerate(segments):
        if not isinstance(segment, dict):
            raise ValueError(f"segment {index} must be an object")
        segment_id = segment.get("id")
        if not isinstance(segment_id, str) or not segment_id.strip():
            raise ValueError(f"segment {index} needs a non-empty string id")
        if segment_id in seen_ids:
            raise ValueError(f"duplicate segment id: {segment_id}")
        seen_ids.add(segment_id)
        if not isinstance(segment.get("text"), str) or not segment["text"].strip():
            raise ValueError(f"segment {segment_id} needs non-empty text")
        start = _number(segment.get("start"), f"segment {segment_id} start")
        end = _number(segment.get("end"), f"segment {segment_id} end")
        if start < 0 or end <= start:
            raise ValueError(f"segment {segment_id} has invalid time range {start}..{end}")
        if start < previous_start:
            raise ValueError(f"segments are not sorted by start time at {segment_id}")
        if duration_seconds is not None and end > duration_seconds + 0.05:
            raise ValueError(f"segment {segment_id} ends after audio duration ({duration_seconds}s)")
        words = segment.get("words", [])
        if not isinstance(words, list):
            raise ValueError(f"segment {segment_id} words must be an array")
        for word in words:
            if not isinstance(word, dict) or not isinstance(word.get("text"), str) or not word["text"]:
                raise ValueError(f"segment {segment_id} contains a malformed word")
            word_start = _number(word.get("start"), f"word start in {segment_id}")
            word_end = _number(word.get("end"), f"word end in {segment_id}")
            if word_start < start - 0.05 or word_end > end + 0.05 or word_end <= word_start:
                raise ValueError(f"word timing is outside segment bounds: {word}")
        previous_start = start
    return segments


def _probe_duration(audio_path: Path, declared_duration: float | None) -> float:
    probed: float | None = None
    if audio_path.suffix.casefold() == ".wav":
        try:
            with wave.open(str(audio_path), "rb") as wav:
                rate = wav.getframerate()
                if rate <= 0:
                    raise ValueError("WAV has an invalid frame rate")
                probed = wav.getnframes() / rate
        except (wave.Error, OSError) as error:
            raise ValueError(f"cannot read WAV duration: {error}") from error
    else:
        ffprobe = shutil.which("ffprobe")
        if ffprobe:
            result = subprocess.run(
                [ffprobe, "-v", "error", "-show_entries", "format=duration",
                 "-of", "default=noprint_wrappers=1:nokey=1", str(audio_path)],
                check=False, capture_output=True, text=True, timeout=15
            )
            if result.returncode == 0:
                try:
                    probed = _number(result.stdout.strip(), "ffprobe duration")
                except ValueError:
                    probed = None
            if probed is None:
                detail = result.stderr.strip() or "ffprobe returned no duration"
                raise ValueError(f"cannot read audio duration with ffprobe: {detail}")
    if probed is not None and probed <= 0:
        raise ValueError("audio duration must be positive")
    if probed is not None and declared_duration is not None and abs(probed - declared_duration) > 0.25:
        raise ValueError(
            f"declared duration {declared_duration}s does not match measured audio duration {probed}s"
        )
    duration = probed if probed is not None else declared_duration
    if duration is None:
        raise ValueError(
            "cannot determine this audio format's duration; install ffprobe or pass --duration-seconds"
        )
    if not math.isfinite(duration) or duration <= 0:
        raise ValueError("audio duration must be a finite positive number")
    return float(duration)


def import_transcript(
    root: Path,
    input_path: Path,
    audio_argument: str,
    *,
    language: str = "en",
    alignment_method: str = "manual",
    declared_duration: float | None = None,
    transcript_id: str | None = None,
    replace: bool = False,
) -> dict[str, Any]:
    root = root.expanduser().resolve()
    db_path = root / "episode.sqlite3"
    input_path = input_path.expanduser().resolve()
    if not db_path.is_file():
        raise ValueError(f"episode database not found: {db_path}")
    if not input_path.is_file():
        raise ValueError(f"transcript JSON not found: {input_path}")
    audio_candidate = Path(audio_argument).expanduser()
    audio_path = (audio_candidate if audio_candidate.is_absolute() else root / audio_candidate).resolve()
    try:
        audio_relative_path = audio_path.relative_to(root).as_posix()
    except ValueError as error:
        raise ValueError("audio path must stay inside the episode root") from error
    if not audio_path.is_file():
        raise ValueError(f"audio file not found: {audio_path}")
    if not language.strip() or not alignment_method.strip():
        raise ValueError("language and alignment method must not be empty")

    payload = json.loads(input_path.read_text(encoding="utf-8"))
    audio_meta = payload.get("audio", {}) if isinstance(payload, dict) else {}
    if audio_meta is None:
        audio_meta = {}
    if not isinstance(audio_meta, dict):
        raise ValueError("audio metadata must be a JSON object")
    declared_path = audio_meta.get("path")
    if declared_path:
        declared_path_obj = Path(str(declared_path)).expanduser()
        declared_abs = (declared_path_obj if declared_path_obj.is_absolute() else root / declared_path_obj).resolve()
        if declared_abs != audio_path:
            raise ValueError("input audio.path does not match --audio")
    actual_sha = sha256_file(audio_path)
    expected_sha = audio_meta.get("sha256")
    if expected_sha and str(expected_sha).casefold() != actual_sha:
        raise ValueError("input audio.sha256 does not match the selected audio file")

    supplied_duration = declared_duration
    if supplied_duration is None and audio_meta.get("duration_seconds") is not None:
        supplied_duration = _number(audio_meta["duration_seconds"], "audio.duration_seconds")
    duration = _probe_duration(audio_path, supplied_duration)
    if audio_meta.get("duration_seconds") is not None:
        metadata_duration = _number(audio_meta["duration_seconds"], "audio.duration_seconds")
        if abs(metadata_duration - duration) > 0.25:
            raise ValueError("input audio.duration_seconds does not match the measured/declared duration")

    segments = validate_payload(payload, duration)
    canonical_payload = dict(payload)
    canonical_payload["audio"] = {
        "path": audio_relative_path,
        "sha256": actual_sha,
        "duration_seconds": round(duration, 6),
        "language": language,
        "alignment_method": alignment_method,
    }
    fingerprint_input = json.dumps(canonical_payload, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    generated_id = "TR-" + hashlib.sha256(
        (actual_sha + "\n" + fingerprint_input + "\n" + alignment_method).encode("utf-8")
    ).hexdigest()[:12].upper()
    final_transcript_id = transcript_id or str(audio_meta.get("transcript_id") or generated_id)
    if not final_transcript_id.strip():
        raise ValueError("transcript ID must not be empty")
    canonical_payload["audio"]["transcript_id"] = final_transcript_id

    now = datetime.now(timezone.utc).isoformat()
    with sqlite3.connect(db_path) as connection:
        from db_contracts import ensure_contract_schema
        ensure_contract_schema(connection)
        existing = connection.execute(
            "SELECT audio_relative_path, audio_sha256, duration_seconds FROM transcripts WHERE transcript_id=?",
            (final_transcript_id,)
        ).fetchone()
        if existing and not replace:
            raise ValueError(
                f"transcript {final_transcript_id} already exists; pass --replace to replace this exact version"
            )
        connection.execute("PRAGMA foreign_keys = ON")
        with connection:
            if existing:
                connection.execute("DELETE FROM transcript_segments WHERE transcript_id=?", (final_transcript_id,))
                connection.execute(
                    "UPDATE transcripts SET audio_relative_path=?, audio_sha256=?, duration_seconds=?, "
                    "language=?, alignment_method=?, created_at=? WHERE transcript_id=?",
                    (audio_relative_path, actual_sha, duration, language, alignment_method, now, final_transcript_id)
                )
                connection.execute("DELETE FROM scenes WHERE transcript_id=?", (final_transcript_id,))
                connection.execute("DELETE FROM scene_plans WHERE transcript_id=?", (final_transcript_id,))
            else:
                connection.execute(
                    "INSERT INTO transcripts(transcript_id, audio_relative_path, audio_sha256, duration_seconds, "
                    "language, alignment_method, created_at) VALUES(?, ?, ?, ?, ?, ?, ?)",
                    (final_transcript_id, audio_relative_path, actual_sha, duration, language, alignment_method, now)
                )
            for segment in segments:
                connection.execute(
                    "INSERT INTO transcript_segments(transcript_id, segment_id, start_seconds, end_seconds, "
                    "text, words_json, speaker, confidence, created_at) VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (final_transcript_id, str(segment["id"]), float(segment["start"]), float(segment["end"]),
                     segment["text"].strip(), json.dumps(segment.get("words", []), ensure_ascii=False),
                     segment.get("speaker"), segment.get("confidence"), now)
                )
            connection.execute(
                "INSERT INTO activity_log(created_at, actor, action, entity_type, entity_id, details_json) "
                "VALUES(?, ?, ?, ?, ?, ?)",
                (now, "import_timed_transcript.py", "transcript_imported", "transcript", final_transcript_id,
                 json.dumps({
                     "audio_path": audio_relative_path, "audio_sha256": actual_sha,
                     "duration_seconds": duration, "segments": len(segments),
                     "replace": replace, "alignment_method": alignment_method,
                 }, ensure_ascii=False))
            )

    destination = root / "transcript" / f"{final_transcript_id}.json"
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(
        json.dumps(canonical_payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    return {
        "transcript_id": final_transcript_id,
        "audio_relative_path": audio_relative_path,
        "audio_sha256": actual_sha,
        "duration_seconds": duration,
        "segment_count": len(segments),
        "destination": destination,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", required=True)
    parser.add_argument("--input", required=True)
    parser.add_argument("--audio", required=True, help="Exact narration audio file, relative to episode root")
    parser.add_argument("--language", default="en")
    parser.add_argument("--alignment-method", default="manual",
                        help="How timings were produced, e.g. whisperx, forced-alignment, manual")
    parser.add_argument("--duration-seconds", type=float,
                        help="Duration for formats that cannot be probed; WAV/ffprobe durations are measured")
    parser.add_argument("--transcript-id", help="Optional stable ID; generated from audio and transcript by default")
    parser.add_argument("--replace", action="store_true",
                        help="Replace segments for this transcript ID, retaining other audio/transcript versions")
    args = parser.parse_args()
    try:
        result = import_transcript(
            Path(args.root), Path(args.input), args.audio, language=args.language,
            alignment_method=args.alignment_method, declared_duration=args.duration_seconds,
            transcript_id=args.transcript_id, replace=args.replace,
        )
    except (OSError, ValueError, json.JSONDecodeError, sqlite3.Error, subprocess.SubprocessError) as error:
        parser.error(f"cannot import transcript: {error}")
    print(f"Imported {result['segment_count']} segments as {result['transcript_id']}")
    print(f"Audio: {result['audio_relative_path']} | SHA-256: {result['audio_sha256']}")
    print(f"Duration: {result['duration_seconds']:.3f}s | Canonical transcript: {result['destination']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
