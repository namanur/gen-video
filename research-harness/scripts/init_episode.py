#!/usr/bin/env python3
"""Create a local episode workspace and its SQLite catalog. Standard library only."""
from __future__ import annotations

import argparse
import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

SCHEMA = """
PRAGMA foreign_keys = ON;
CREATE TABLE IF NOT EXISTS project_meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS sources (
    source_id TEXT PRIMARY KEY, canonical_url TEXT NOT NULL UNIQUE, original_url TEXT,
    title TEXT NOT NULL, author TEXT, publisher TEXT, published_date TEXT,
    accessed_at TEXT NOT NULL, source_type TEXT NOT NULL, local_path TEXT, sha256 TEXT,
    source_quality TEXT, rights_status TEXT NOT NULL DEFAULT 'unknown', excerpt TEXT, notes TEXT
);
CREATE TABLE IF NOT EXISTS claims (
    claim_id TEXT PRIMARY KEY, statement TEXT NOT NULL, classification TEXT NOT NULL DEFAULT 'empirical',
    confidence REAL, status TEXT NOT NULL DEFAULT 'proposed', notes TEXT, created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS claim_sources (
    claim_id TEXT NOT NULL REFERENCES claims(claim_id) ON DELETE CASCADE,
    source_id TEXT NOT NULL REFERENCES sources(source_id) ON DELETE CASCADE,
    evidence_role TEXT NOT NULL DEFAULT 'supports', locator TEXT, excerpt TEXT, directness TEXT,
    PRIMARY KEY (claim_id, source_id, evidence_role, locator)
);
CREATE TABLE IF NOT EXISTS assets (
    asset_id TEXT PRIMARY KEY, asset_type TEXT NOT NULL, relative_path TEXT NOT NULL UNIQUE,
    filename TEXT NOT NULL, description TEXT NOT NULL, origin_url TEXT,
    source_id TEXT REFERENCES sources(source_id) ON DELETE SET NULL, sha256 TEXT NOT NULL,
    mime_type TEXT, capture_method TEXT, rights_status TEXT NOT NULL DEFAULT 'unknown',
    review_status TEXT NOT NULL DEFAULT 'candidate', created_at TEXT NOT NULL,
    metadata_json TEXT NOT NULL DEFAULT '{}'
);
CREATE TABLE IF NOT EXISTS asset_tags (
    asset_id TEXT NOT NULL REFERENCES assets(asset_id) ON DELETE CASCADE,
    tag TEXT NOT NULL, PRIMARY KEY (asset_id, tag)
);
CREATE TABLE IF NOT EXISTS citations (
    citation_id TEXT PRIMARY KEY, claim_id TEXT REFERENCES claims(claim_id) ON DELETE SET NULL,
    source_id TEXT NOT NULL REFERENCES sources(source_id) ON DELETE CASCADE,
    locator TEXT NOT NULL, exact_quote TEXT, citation_style TEXT, note TEXT
);
CREATE TABLE IF NOT EXISTS transcript_segments (
    segment_id TEXT PRIMARY KEY, start_seconds REAL NOT NULL, end_seconds REAL NOT NULL,
    text TEXT NOT NULL, words_json TEXT NOT NULL DEFAULT '[]', speaker TEXT, confidence REAL,
    created_at TEXT NOT NULL, CHECK(start_seconds >= 0), CHECK(end_seconds > start_seconds)
);
CREATE TABLE IF NOT EXISTS scenes (
    scene_id TEXT PRIMARY KEY, start_seconds REAL NOT NULL, end_seconds REAL NOT NULL,
    narration_text TEXT, visual_goal TEXT NOT NULL, on_screen_text_json TEXT NOT NULL DEFAULT '[]',
    status TEXT NOT NULL DEFAULT 'draft', notes TEXT,
    CHECK(start_seconds >= 0), CHECK(end_seconds > start_seconds)
);
CREATE TABLE IF NOT EXISTS scene_assets (
    scene_id TEXT NOT NULL REFERENCES scenes(scene_id) ON DELETE CASCADE,
    asset_id TEXT NOT NULL REFERENCES assets(asset_id) ON DELETE RESTRICT,
    role TEXT NOT NULL DEFAULT 'primary', timing_json TEXT NOT NULL DEFAULT '{}',
    PRIMARY KEY (scene_id, asset_id, role)
);
CREATE TABLE IF NOT EXISTS activity_log (
    event_id INTEGER PRIMARY KEY AUTOINCREMENT, created_at TEXT NOT NULL, actor TEXT NOT NULL,
    action TEXT NOT NULL, entity_type TEXT, entity_id TEXT, details_json TEXT NOT NULL DEFAULT '{}'
);
CREATE INDEX IF NOT EXISTS idx_assets_type ON assets(asset_type);
CREATE INDEX IF NOT EXISTS idx_assets_review ON assets(review_status);
CREATE INDEX IF NOT EXISTS idx_assets_rights ON assets(rights_status);
CREATE INDEX IF NOT EXISTS idx_assets_hash ON assets(sha256);
CREATE INDEX IF NOT EXISTS idx_sources_type ON sources(source_type);
CREATE INDEX IF NOT EXISTS idx_claims_status ON claims(status);
CREATE INDEX IF NOT EXISTS idx_transcript_time ON transcript_segments(start_seconds, end_seconds);
CREATE INDEX IF NOT EXISTS idx_scenes_time ON scenes(start_seconds, end_seconds);
CREATE INDEX IF NOT EXISTS idx_asset_tags_tag ON asset_tags(tag);
"""

DIRECTORIES = [
    "research", "sources/pages", "sources/pdfs", "sources/screenshots", "sources/books",
    "assets/images", "assets/diagrams", "assets/video", "assets/screen-recordings",
    "assets/audio", "script", "narration/original", "narration/final", "transcript",
    "edit/hyperframes", "exports", "logs"
]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", required=True, help="Episode directory to create")
    parser.add_argument("--slug", required=True)
    parser.add_argument("--title", required=True)
    parser.add_argument("--topic", required=True)
    parser.add_argument("--channel", default="brain-on-internet")
    args = parser.parse_args()

    root = Path(args.root).expanduser().resolve()
    db_path = root / "episode.sqlite3"
    if db_path.exists():
        parser.error(f"episode database already exists: {db_path}")
    for item in DIRECTORIES:
        (root / item).mkdir(parents=True, exist_ok=True)

    created = datetime.now(timezone.utc).isoformat()
    metadata = {
        "slug": args.slug, "title": args.title, "topic": args.topic, "channel": args.channel,
        "created_at": created, "status": "research", "target_duration_seconds": 600,
        "frame": {"width": 1920, "height": 1080, "fps": 30}
    }
    (root / "episode.json").write_text(json.dumps(metadata, indent=2, ensure_ascii=False) + chr(10),
                                        encoding="utf-8")
    with sqlite3.connect(db_path) as connection:
        connection.executescript(SCHEMA)
        connection.executemany(
            "INSERT INTO project_meta(key, value) VALUES(?, ?)",
            [(key, json.dumps(value, ensure_ascii=False) if isinstance(value, (dict, list)) else str(value))
             for key, value in metadata.items()]
        )
        connection.execute(
            "INSERT INTO activity_log(created_at, actor, action, entity_type, entity_id, details_json) "
            "VALUES(?, ?, ?, ?, ?, ?)",
            (created, "init_episode.py", "episode_initialized", "project", args.slug,
             json.dumps({"root": str(root)}, ensure_ascii=False))
        )
    print(f"Episode initialized: {root}")
    print(f"Database: {db_path}")
    print("No network calls, model calls, or paid API calls were made.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
