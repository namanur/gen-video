#!/usr/bin/env python3
"""Standard-library tests for the research harness data contracts."""
from __future__ import annotations

import importlib.util
import tempfile
import unittest
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


class TestTimedTranscriptContract(unittest.TestCase):
    def test_valid_word_timestamps(self):
        payload = {"segments": [{
            "id": "seg-1", "start": 0.0, "end": 1.2, "text": "Testing one two.",
            "words": [
                {"text": "Testing", "start": 0.0, "end": 0.4},
                {"text": "one", "start": 0.45, "end": 0.7},
                {"text": "two.", "start": 0.71, "end": 1.0}
            ]
        }]}
        self.assertEqual(len(transcript.validate_payload(payload)), 1)

    def test_rejects_non_monotonic_segments(self):
        payload = {"segments": [
            {"id": "a", "start": 2, "end": 3, "text": "Later"},
            {"id": "b", "start": 1, "end": 1.5, "text": "Earlier"}
        ]}
        with self.assertRaises(ValueError):
            transcript.validate_payload(payload)

    def test_rejects_word_outside_segment(self):
        payload = {"segments": [{
            "id": "a", "start": 0, "end": 1, "text": "Hello",
            "words": [{"text": "Hello", "start": 1.1, "end": 1.2}]
        }]}
        with self.assertRaises(ValueError):
            transcript.validate_payload(payload)


class TestEpisodeSchema(unittest.TestCase):
    def test_schema_creates_required_tables(self):
        import sqlite3
        with tempfile.TemporaryDirectory() as temp:
            db = Path(temp) / "test.sqlite3"
            with sqlite3.connect(db) as connection:
                connection.executescript(init_episode.SCHEMA)
                tables = {
                    row[0] for row in connection.execute(
                        "SELECT name FROM sqlite_master WHERE type='table'"
                    )
                }
            self.assertIn("assets", tables)
            self.assertIn("sources", tables)
            self.assertIn("claims", tables)
            self.assertIn("transcript_segments", tables)
            self.assertIn("scene_assets", tables)


if __name__ == "__main__":
    unittest.main()
