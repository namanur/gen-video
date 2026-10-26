#!/usr/bin/env python3
"""Standard-library regression tests for the research harness data contracts."""
from __future__ import annotations

import hashlib
import importlib.util
import json
import sqlite3
import sys
import tempfile
import unittest
import wave
from pathlib import Path

HERE = Path(__file__).resolve()
HARNESS = HERE.parents[1]
SCRIPTS = HARNESS / "scripts"
FIXTURES = HERE / "fixtures"
sys.path.insert(0, str(SCRIPTS))


def load_module(name: str, filename: str):
    spec = importlib.util.spec_from_file_location(name, SCRIPTS / filename)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Cannot load {filename}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


init_episode = load_module("init_episode", "init_episode.py")
transcript = load_module("transcript_contract", "import_timed_transcript.py")
scene_validator = load_module("scene_validator", "validate_scene_plan.py")
from db_contracts import ensure_contract_schema


def write_test_wav(path: Path, seconds: float = 3.0) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    sample_rate = 8000
    with wave.open(str(path), "wb") as audio:
        audio.setnchannels(1)
        audio.setsampwidth(2)
        audio.setframerate(sample_rate)
        audio.writeframes(b"\x00\x00" * int(sample_rate * seconds))


def build_fixture_episode(root: Path) -> dict:
    root.mkdir(parents=True, exist_ok=True)
    episode = json.loads((FIXTURES / "episode.json").read_text(encoding="utf-8"))
    (root / "episode.json").write_text(json.dumps(episode, indent=2) + "\n", encoding="utf-8")
    (root / "narration" / "final").mkdir(parents=True, exist_ok=True)
    (root / "transcript").mkdir(parents=True, exist_ok=True)
    (root / "edit").mkdir(parents=True, exist_ok=True)
    audio_path = root / "narration" / "final" / "voice.wav"
    write_test_wav(audio_path, 3.0)
    artifact_path = root / "assets" / "source-card.txt"
    artifact_path.parent.mkdir(parents=True, exist_ok=True)
    artifact_path.write_text("A local fixture artifact for contract tests.\n", encoding="utf-8")
    now = "2026-10-26T00:00:00+00:00"
    digest = hashlib.sha256(artifact_path.read_bytes()).hexdigest()

    with sqlite3.connect(root / "episode.sqlite3") as connection:
        connection.executescript(init_episode.SCHEMA)
        ensure_contract_schema(connection)
        connection.execute(
            "INSERT INTO sources(source_id, canonical_url, title, accessed_at, source_type) "
            "VALUES(?, ?, ?, ?, ?)",
            ("SRC-001", "https://example.test/source", "Fixture source", now, "article"),
        )
        connection.execute(
            "INSERT INTO assets(asset_id, asset_type, relative_path, filename, description, origin_url, "
            "source_id, sha256, mime_type, capture_method, rights_status, review_status, created_at, metadata_json) "
            "VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            ("A-FIXTURE", "image", "assets/source-card.txt", "source-card.txt",
             "Fixture visual artifact", "https://example.test/source", "SRC-001", digest,
             "text/plain", "test-fixture", "creator_owned", "approved", now, "{}"),
        )
    return {"audio_path": audio_path, "artifact_path": artifact_path}


class TestTimedTranscriptContract(unittest.TestCase):
    def test_valid_word_timestamps(self):
        payload = {"segments": [{
            "id": "seg-1", "start": 0.0, "end": 1.2, "text": "Testing one two.",
            "words": [
                {"text": "Testing", "start": 0.0, "end": 0.4},
                {"text": "one", "start": 0.45, "end": 0.7},
                {"text": "two.", "start": 0.71, "end": 1.0},
            ],
        }]}
        self.assertEqual(len(transcript.validate_payload(payload, 3.0)), 1)

    def test_rejects_non_monotonic_segments(self):
        payload = {"segments": [
            {"id": "a", "start": 2, "end": 3, "text": "Later"},
            {"id": "b", "start": 1, "end": 1.5, "text": "Earlier"},
        ]}
        with self.assertRaises(ValueError):
            transcript.validate_payload(payload)

    def test_rejects_word_outside_segment(self):
        payload = {"segments": [{
            "id": "a", "start": 0, "end": 1, "text": "Hello",
            "words": [{"text": "Hello", "start": 1.1, "end": 1.2}],
        }]}
        with self.assertRaises(ValueError):
            transcript.validate_payload(payload)

    def test_rejects_duplicate_segment_ids(self):
        payload = {"segments": [
            {"id": "same", "start": 0, "end": 0.5, "text": "One"},
            {"id": "same", "start": 0.5, "end": 1, "text": "Two"},
        ]}
        with self.assertRaisesRegex(ValueError, "duplicate segment id"):
            transcript.validate_payload(payload)

    def test_rejects_segment_past_audio_duration(self):
        payload = {"segments": [{"id": "late", "start": 2.9, "end": 3.1, "text": "Late"}]}
        with self.assertRaisesRegex(ValueError, "after audio duration"):
            transcript.validate_payload(payload, 3.0)


class TestEpisodeSchema(unittest.TestCase):
    def test_schema_creates_required_tables(self):
        with tempfile.TemporaryDirectory() as temp:
            db = Path(temp) / "test.sqlite3"
            with sqlite3.connect(db) as connection:
                connection.executescript(init_episode.SCHEMA)
                ensure_contract_schema(connection)
                tables = {
                    row[0] for row in connection.execute(
                        "SELECT name FROM sqlite_master WHERE type='table'"
                    )
                }
            for table in (
                "assets", "sources", "claims", "transcripts", "transcript_segments",
                "scene_plans", "scenes", "scene_assets", "scene_citations",
                "scene_transcript_segments",
            ):
                self.assertIn(table, tables)

    def test_legacy_segments_are_archived_not_assumed_to_match_new_audio(self):
        with tempfile.TemporaryDirectory() as temp:
            db = Path(temp) / "legacy.sqlite3"
            with sqlite3.connect(db) as connection:
                # Reproduce the original schema: no transcripts table and a segment_id-only key.
                connection.executescript("""
                    CREATE TABLE sources(source_id TEXT PRIMARY KEY);
                    CREATE TABLE scenes(
                        scene_id TEXT PRIMARY KEY, start_seconds REAL NOT NULL, end_seconds REAL NOT NULL,
                        narration_text TEXT, visual_goal TEXT NOT NULL,
                        on_screen_text_json TEXT NOT NULL DEFAULT '[]',
                        status TEXT NOT NULL DEFAULT 'draft', notes TEXT
                    );
                    CREATE TABLE transcript_segments(
                        segment_id TEXT PRIMARY KEY, start_seconds REAL NOT NULL, end_seconds REAL NOT NULL,
                        text TEXT NOT NULL, words_json TEXT NOT NULL DEFAULT '[]', speaker TEXT, confidence REAL,
                        created_at TEXT NOT NULL
                    );
                """)
                connection.execute(
                    "INSERT INTO transcript_segments(segment_id, start_seconds, end_seconds, text, created_at) "
                    "VALUES('old-1', 0, 1, 'Old unbound timing', '2026-10-25T00:00:00+00:00')"
                )
                ensure_contract_schema(connection)
                archived = connection.execute(
                    "SELECT text FROM transcript_segments_legacy_unbound WHERE segment_id='old-1'"
                ).fetchone()
                active_count = connection.execute("SELECT COUNT(*) FROM transcript_segments").fetchone()[0]
            self.assertEqual(archived[0], "Old unbound timing")
            self.assertEqual(active_count, 0)


class TestAudioAndSceneContracts(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name) / "fixture"
        self.paths = build_fixture_episode(self.root)
        self.input_path = self.root / "transcript" / "input.json"
        payload = json.loads((FIXTURES / "timed_transcript.json").read_text(encoding="utf-8"))
        payload["audio"] = {"path": "narration/final/voice.wav", "duration_seconds": 3.0}
        self.input_path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
        self.transcript_result = transcript.import_transcript(
            self.root, self.input_path, "narration/final/voice.wav",
            language="en", alignment_method="test-fixture", declared_duration=3.0,
        )
        self.plan_path = self.root / "edit" / "scene_plan.json"
        self.write_plan()

    def write_plan(self, *, segment_ids=None, citation_source_ids=None, start=0.0, end=1.0):
        audio = self.transcript_result
        plan = {
            "version": 1,
            "plan_id": "plan-fixture-v001",
            "project_slug": "fixture-episode",
            "transcript_id": audio["transcript_id"],
            "canvas": {"width": 1080, "height": 1920, "fps": 30},
            "audio": {
                "path": audio["audio_relative_path"],
                "sha256": audio["audio_sha256"],
                "duration_seconds": audio["duration_seconds"],
            },
            "scenes": [{
                "scene_id": "SC-FIXTURE-001",
                "start_seconds": start,
                "end_seconds": end,
                "transcript_segment_ids": segment_ids if segment_ids is not None else ["seg-001"],
                "narration_text": "A fixture line of narration.",
                "visual_goal": "Show the fixture evidence artifact.",
                "asset_ids": ["A-FIXTURE"],
                "on_screen_text": [],
                "citation_source_ids": citation_source_ids if citation_source_ids is not None else ["SRC-001"],
                "motion": "static",
                "notes": "Test fixture only.",
            }],
        }
        self.plan_path.write_text(json.dumps(plan, indent=2) + "\n", encoding="utf-8")

    def test_import_binds_segments_to_exact_audio_hash_and_duration(self):
        with sqlite3.connect(self.root / "episode.sqlite3") as connection:
            row = connection.execute(
                "SELECT t.audio_relative_path, t.audio_sha256, t.duration_seconds, "
                "COUNT(s.segment_id) FROM transcripts t JOIN transcript_segments s "
                "ON s.transcript_id=t.transcript_id WHERE t.transcript_id=? GROUP BY t.transcript_id",
                (self.transcript_result["transcript_id"],),
            ).fetchone()
        self.assertEqual(row[0], "narration/final/voice.wav")
        self.assertEqual(row[1], hashlib.sha256(self.paths["audio_path"].read_bytes()).hexdigest())
        self.assertAlmostEqual(row[2], 3.0)
        self.assertEqual(row[3], 2)

    def test_rejects_mismatched_declared_wav_duration(self):
        with self.assertRaisesRegex(ValueError, "does not match measured audio duration"):
            transcript.import_transcript(
                self.root, self.input_path, "narration/final/voice.wav",
                alignment_method="test-fixture", declared_duration=4.0, transcript_id="TR-BAD-DURATION",
            )

    def test_persists_validated_scene_and_relationships(self):
        errors, scene_count, asset_count = scene_validator.validate_scene_plan(
            self.root, self.plan_path, persist=True
        )
        self.assertEqual(errors, [])
        self.assertEqual((scene_count, asset_count), (1, 1))
        with sqlite3.connect(self.root / "episode.sqlite3") as connection:
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM scene_plans").fetchone()[0], 1)
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM scenes").fetchone()[0], 1)
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM scene_assets").fetchone()[0], 1)
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM scene_citations").fetchone()[0], 1)
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM scene_transcript_segments").fetchone()[0], 1)

    def test_rejects_stale_audio_after_file_changes(self):
        with self.paths["audio_path"].open("ab") as audio:
            audio.write(b"changed")
        errors, _, _ = scene_validator.validate_scene_plan(self.root, self.plan_path, persist=True)
        self.assertTrue(any("audio.sha256 does not match" in issue for issue in errors))
        with sqlite3.connect(self.root / "episode.sqlite3") as connection:
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM scene_plans").fetchone()[0], 0)

    def test_rejects_unknown_transcript_segment(self):
        self.write_plan(segment_ids=["not-a-real-segment"])
        errors, _, _ = scene_validator.validate_scene_plan(self.root, self.plan_path, persist=True)
        self.assertTrue(any("unknown transcript segment not-a-real-segment" in issue for issue in errors))
        with sqlite3.connect(self.root / "episode.sqlite3") as connection:
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM scenes").fetchone()[0], 0)

    def test_rejects_scene_that_does_not_cover_its_segment(self):
        self.write_plan(start=0.2, end=0.8)
        errors, _, _ = scene_validator.validate_scene_plan(self.root, self.plan_path, persist=True)
        self.assertTrue(any("does not cover transcript segment" in issue for issue in errors))

    def test_rejects_missing_citation_source(self):
        self.write_plan(citation_source_ids=["SRC-MISSING"])
        errors, _, _ = scene_validator.validate_scene_plan(self.root, self.plan_path, persist=True)
        self.assertTrue(any("unknown citation source_id SRC-MISSING" in issue for issue in errors))
        with sqlite3.connect(self.root / "episode.sqlite3") as connection:
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM scenes").fetchone()[0], 0)


if __name__ == "__main__":
    unittest.main()
