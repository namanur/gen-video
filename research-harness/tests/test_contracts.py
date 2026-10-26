#!/usr/bin/env python3
"""Standard-library tests for the research harness data contracts."""
from __future__ import annotations

import copy
import hashlib
import importlib.util
import json
import sqlite3
import tempfile
import unittest
import wave
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve()
SCRIPTS = HERE.parents[1] / "scripts"


def load_module(name: str, filename: str):
    spec = importlib.util.spec_from_file_location(name, SCRIPTS / filename)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Cannot load {filename}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


init_episode = load_module("init_episode", "init_episode.py")
transcript = load_module("transcript_contract", "import_timed_transcript.py")
validator = load_module("scene_plan_validator", "validate_scene_plan.py")


def write_wav(path: Path, seconds: float = 1.0, rate: int = 8000) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(rate)
        handle.writeframes(b"\x00\x00" * int(rate * seconds))


def base_payload() -> dict:
    return {
        "schema_version": 1,
        "language": "en",
        "audio_path": "narration/final/voice-v001.wav",
        "duration_seconds": 1.0,
        "alignment_method": "test_fixture",
        "segments": [
            {
                "id": "seg-0001",
                "start": 0.0,
                "end": 0.4,
                "text": "First test segment.",
                "words": [
                    {"text": "First", "start": 0.0, "end": 0.15},
                    {"text": "test", "start": 0.16, "end": 0.28},
                    {"text": "segment.", "start": 0.29, "end": 0.4},
                ],
            },
            {"id": "seg-0002", "start": 0.4, "end": 0.8, "text": "Second test segment."},
        ],
    }


class TestTimedTranscriptContract(unittest.TestCase):
    def test_valid_word_timestamps(self):
        self.assertEqual(len(transcript.validate_payload(base_payload())), 2)

    def test_rejects_non_monotonic_segments(self):
        payload = base_payload()
        payload["segments"].reverse()
        with self.assertRaises(ValueError):
            transcript.validate_payload(payload)

    def test_rejects_word_outside_segment(self):
        payload = base_payload()
        payload["segments"][0]["words"][0]["start"] = 0.45
        with self.assertRaises(ValueError):
            transcript.validate_payload(payload)

    def test_rejects_segment_past_duration(self):
        payload = base_payload()
        payload["segments"][1]["end"] = 1.5
        with self.assertRaises(ValueError):
            transcript.validate_payload(payload)

    def test_rejects_duplicate_segment_ids(self):
        payload = base_payload()
        payload["segments"][1]["id"] = payload["segments"][0]["id"]
        with self.assertRaises(ValueError):
            transcript.validate_payload(payload)

    def test_rejects_missing_audio_path(self):
        payload = base_payload()
        payload.pop("audio_path")
        with self.assertRaises(ValueError):
            transcript.validate_payload(payload)


class TestEpisodeSchema(unittest.TestCase):
    def test_schema_creates_required_tables(self):
        with tempfile.TemporaryDirectory() as temp:
            db = Path(temp) / "test.sqlite3"
            with sqlite3.connect(db) as connection:
                connection.executescript(init_episode.SCHEMA)
                tables = {
                    row[0] for row in connection.execute(
                        "SELECT name FROM sqlite_master WHERE type='table'"
                    )
                }
            for table in (
                "assets", "sources", "claims", "transcripts", "transcript_segments",
                "scenes", "scene_assets", "scene_segments", "scene_sources",
            ):
                self.assertIn(table, tables)


class EpisodeFixture(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        init_episode.create_episode(
            self.root, "ragebait-001",
            "Why Ragebait Works Even When You Know It Is Ragebait",
            "Psychology of outrage and online incentives",
        )
        write_wav(self.root / "narration/final/voice-v001.wav")

    def tearDown(self):
        self.temp.cleanup()

    def ingest(self, payload: dict | None = None, transcript_id: str = "T-001",
               replace: bool = False) -> dict:
        return transcript.ingest_transcript(
            self.root, payload or base_payload(), transcript_id=transcript_id, replace=replace
        )

    def register_source(self, source_id: str = "SRC-001") -> None:
        now = datetime.now(timezone.utc).isoformat()
        with sqlite3.connect(self.root / "episode.sqlite3") as connection:
            connection.execute(
                "INSERT INTO sources(source_id, canonical_url, title, accessed_at, source_type) "
                "VALUES(?, ?, ?, ?, ?)",
                (source_id, f"https://example.org/{source_id.lower()}", "Example study", now, "paper")
            )

    def register_asset(self, asset_id: str = "A-TEST000001",
                       review_status: str = "approved") -> str:
        relative = f"sources/screenshots/{asset_id}.png"
        target = self.root / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(b"fixture image bytes")
        digest = hashlib.sha256(target.read_bytes()).hexdigest()
        now = datetime.now(timezone.utc).isoformat()
        with sqlite3.connect(self.root / "episode.sqlite3") as connection:
            connection.execute(
                "INSERT INTO assets(asset_id, asset_type, relative_path, filename, description, "
                "source_id, sha256, mime_type, capture_method, rights_status, review_status, created_at) "
                "VALUES(?, 'screenshot', ?, ?, 'Fixture results table', 'SRC-001', ?, "
                "'image/png', 'fixture', 'fair_use_review', ?, ?)",
                (asset_id, relative, target.name, digest, review_status, now)
            )
        return asset_id

    def valid_plan(self, audio_hash: str) -> dict:
        return {
            "version": 1,
            "project_slug": "ragebait-001",
            "canvas": {"width": 1920, "height": 1080, "fps": 30},
            "audio": {
                "transcript_id": "T-001",
                "path": "narration/final/voice-v001.wav",
                "sha256": audio_hash,
                "duration_seconds": 1.0,
            },
            "scenes": [{
                "scene_id": "SC001",
                "start_seconds": 0.0,
                "end_seconds": 0.9,
                "transcript_segment_ids": ["seg-0001", "seg-0002"],
                "narration_text": "First test segment. Second test segment.",
                "visual_goal": "Show the fixture results table",
                "asset_ids": ["A-TEST000001"],
                "on_screen_text": ["RESULTS"],
                "citation_source_ids": ["SRC-001"],
                "motion": "slow push-in",
                "notes": "Fixture only.",
            }],
        }


class TestImporter(EpisodeFixture):
    def test_import_persists_versioned_transcript(self):
        result = self.ingest()
        self.assertEqual(result["transcript_id"], "T-001")
        self.assertEqual(result["segments"], 2)
        self.assertTrue(result["duration_verified"])
        self.assertEqual(len(result["audio_sha256"]), 64)
        with sqlite3.connect(self.root / "episode.sqlite3") as connection:
            row = connection.execute(
                "SELECT audio_path, audio_sha256, duration_seconds FROM transcripts "
                "WHERE transcript_id = 'T-001'"
            ).fetchone()
            current = connection.execute(
                "SELECT value FROM project_meta WHERE key = 'current_transcript_id'"
            ).fetchone()
            count = connection.execute(
                "SELECT COUNT(*) FROM transcript_segments WHERE transcript_id = 'T-001'"
            ).fetchone()[0]
        self.assertEqual(row[0], "narration/final/voice-v001.wav")
        self.assertEqual(row[1], result["audio_sha256"])
        self.assertEqual(row[2], 1.0)
        self.assertEqual(current[0], "T-001")
        self.assertEqual(count, 2)
        canonical = json.loads((self.root / "transcript/timed_transcript.json").read_text())
        self.assertEqual(canonical["audio_sha256"], result["audio_sha256"])
        self.assertTrue((self.root / "transcript/versions/T-001.json").is_file())

    def test_import_rejects_hash_mismatch(self):
        payload = base_payload()
        payload["audio_sha256"] = "0" * 64
        with self.assertRaisesRegex(ValueError, "different audio"):
            self.ingest(payload)

    def test_import_rejects_duration_mismatch(self):
        payload = base_payload()
        payload["duration_seconds"] = 5.0
        with self.assertRaisesRegex(ValueError, "does not match measured"):
            self.ingest(payload)

    def test_import_rejects_missing_audio(self):
        payload = base_payload()
        payload["audio_path"] = "narration/final/missing.wav"
        with self.assertRaisesRegex(ValueError, "does not exist"):
            self.ingest(payload)

    def test_import_rejects_unreadable_wav(self):
        (self.root / "narration/final/voice-v001.wav").write_bytes(b"not a wave file")
        with self.assertRaisesRegex(ValueError, "may be corrupt"):
            self.ingest()

    def test_replace_transcript_version(self):
        self.ingest()
        payload = base_payload()
        payload["segments"][0]["text"] = "Replacement first segment."
        self.ingest(payload, replace=True)
        with sqlite3.connect(self.root / "episode.sqlite3") as connection:
            transcripts = connection.execute("SELECT COUNT(*) FROM transcripts").fetchone()[0]
            text = connection.execute(
                "SELECT text FROM transcript_segments WHERE segment_id = 'seg-0001'"
            ).fetchone()[0]
        self.assertEqual(transcripts, 1)
        self.assertEqual(text, "Replacement first segment.")

    def test_duplicate_transcript_id_requires_replace(self):
        self.ingest()
        with self.assertRaisesRegex(ValueError, "already exists"):
            self.ingest()

    def test_second_version_can_reuse_segment_ids(self):
        self.ingest()
        self.ingest(transcript_id="T-002")
        with sqlite3.connect(self.root / "episode.sqlite3") as connection:
            transcripts = connection.execute("SELECT COUNT(*) FROM transcripts").fetchone()[0]
            segments = connection.execute("SELECT COUNT(*) FROM transcript_segments").fetchone()[0]
            current = connection.execute(
                "SELECT value FROM project_meta WHERE key = 'current_transcript_id'"
            ).fetchone()[0]
        self.assertEqual((transcripts, segments, current), (2, 4, "T-002"))

    def test_replace_marks_dependent_scenes_stale(self):
        self.ingest()
        with sqlite3.connect(self.root / "episode.sqlite3") as connection:
            connection.execute(
                "INSERT INTO scenes(scene_id, transcript_id, start_seconds, end_seconds, "
                "visual_goal, status) VALUES('SC-OLD', 'T-001', 0.0, 0.8, 'Old scene', 'validated')"
            )
        self.ingest(replace=True)
        with sqlite3.connect(self.root / "episode.sqlite3") as connection:
            status = connection.execute(
                "SELECT status FROM scenes WHERE scene_id = 'SC-OLD'"
            ).fetchone()[0]
        self.assertEqual(status, "stale")


class TestScenePlan(EpisodeFixture):
    def setUp(self):
        super().setUp()
        self.transcript_result = self.ingest()
        self.register_source()
        self.register_asset()

    def plan(self) -> dict:
        return copy.deepcopy(self.valid_plan(self.transcript_result["audio_sha256"]))

    def test_valid_plan_passes_and_saves(self):
        plan = self.plan()
        errors, warnings, summary = validator.validate_plan(self.root, plan)
        self.assertEqual(errors, [])
        self.assertEqual(warnings, [])
        self.assertEqual(summary["scenes"], 1)
        self.assertEqual(validator.save_plan(self.root, plan), 1)
        with sqlite3.connect(self.root / "episode.sqlite3") as connection:
            scene = connection.execute(
                "SELECT transcript_id, status FROM scenes WHERE scene_id = 'SC001'"
            ).fetchone()
            assets = connection.execute("SELECT COUNT(*) FROM scene_assets").fetchone()[0]
            segments = connection.execute("SELECT COUNT(*) FROM scene_segments").fetchone()[0]
            sources = connection.execute("SELECT COUNT(*) FROM scene_sources").fetchone()[0]
        self.assertEqual(scene, ("T-001", "validated"))
        self.assertEqual((assets, segments, sources), (1, 2, 1))

    def test_rejects_unknown_transcript_segment(self):
        plan = self.plan()
        plan["scenes"][0]["transcript_segment_ids"] = ["seg-9999"]
        errors, _, _ = validator.validate_plan(self.root, plan)
        self.assertTrue(any("unknown transcript segment" in error for error in errors))

    def test_rejects_stale_audio_hash(self):
        plan = self.plan()
        plan["audio"]["sha256"] = "f" * 64
        errors, _, _ = validator.validate_plan(self.root, plan)
        self.assertTrue(any("scene plan is stale" in error for error in errors))

    def test_rejects_missing_transcript_id(self):
        plan = self.plan()
        plan["audio"].pop("transcript_id")
        errors, _, _ = validator.validate_plan(self.root, plan)
        self.assertTrue(any("audio.transcript_id is required" in error for error in errors))

    def test_rejects_unknown_citation_source(self):
        plan = self.plan()
        plan["scenes"][0]["citation_source_ids"] = ["SRC-999"]
        errors, _, _ = validator.validate_plan(self.root, plan)
        self.assertTrue(any("unknown citation source_id" in error for error in errors))

    def test_rejects_duplicate_asset_ids(self):
        plan = self.plan()
        plan["scenes"][0]["asset_ids"] = ["A-TEST000001", "A-TEST000001"]
        errors, _, _ = validator.validate_plan(self.root, plan)
        self.assertTrue(any("duplicate asset IDs" in error for error in errors))

    def test_rejects_unapproved_asset(self):
        self.register_asset(asset_id="A-CANDIDATE1", review_status="candidate")
        plan = self.plan()
        plan["scenes"][0]["asset_ids"] = ["A-CANDIDATE1"]
        errors, _, _ = validator.validate_plan(self.root, plan)
        self.assertTrue(any("not render-approved" in error for error in errors))
        errors_with_flag, _, _ = validator.validate_plan(self.root, plan, allow_candidate=True)
        self.assertEqual(errors_with_flag, [])

    def test_rejects_missing_asset_file(self):
        (self.root / "sources/screenshots/A-TEST000001.png").unlink()
        errors, _, _ = validator.validate_plan(self.root, self.plan())
        self.assertTrue(any("asset file is missing" in error for error in errors))

    def test_rejects_scene_that_does_not_overlap_segment(self):
        plan = self.plan()
        plan["scenes"][0]["start_seconds"] = 0.0
        plan["scenes"][0]["end_seconds"] = 0.1
        errors, _, _ = validator.validate_plan(self.root, plan)
        self.assertTrue(any("does not overlap transcript segment" in error for error in errors))

    def test_warns_about_unreferenced_segments(self):
        plan = self.plan()
        plan["scenes"][0]["transcript_segment_ids"] = ["seg-0001"]
        errors, warnings, _ = validator.validate_plan(self.root, plan)
        self.assertEqual(errors, [])
        self.assertTrue(any("not referenced by any scene" in warning for warning in warnings))

    def test_save_replaces_existing_scene(self):
        plan = self.plan()
        validator.save_plan(self.root, plan)
        plan["scenes"][0]["notes"] = "Updated fixture note."
        validator.save_plan(self.root, plan)
        with sqlite3.connect(self.root / "episode.sqlite3") as connection:
            count = connection.execute("SELECT COUNT(*) FROM scenes").fetchone()[0]
            notes = connection.execute("SELECT notes FROM scenes WHERE scene_id = 'SC001'").fetchone()[0]
        self.assertEqual(count, 1)
        self.assertEqual(notes, "Updated fixture note.")


if __name__ == "__main__":
    unittest.main()
