"""Idempotent SQLite migrations for transcript/audio and scene-plan contracts."""
from __future__ import annotations

import sqlite3


def _columns(connection: sqlite3.Connection, table: str) -> dict[str, sqlite3.Row]:
    return {row["name"]: row for row in connection.execute(f"PRAGMA table_info({table})")}


def ensure_contract_schema(connection: sqlite3.Connection) -> None:
    """Upgrade fresh and pre-contract episode databases without discarding old data."""
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    connection.execute("""
        CREATE TABLE IF NOT EXISTS transcripts (
            transcript_id TEXT PRIMARY KEY,
            audio_relative_path TEXT NOT NULL,
            audio_sha256 TEXT NOT NULL,
            duration_seconds REAL NOT NULL CHECK(duration_seconds > 0),
            language TEXT NOT NULL DEFAULT 'en',
            alignment_method TEXT NOT NULL,
            created_at TEXT NOT NULL
        )
    """)

    # Old segments had a globally unique segment_id and no audio-version FK.
    # Preserve them in an explicitly unbound archive; never treat them as aligned.
    if connection.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='transcript_segments'"
    ).fetchone():
        cols = _columns(connection, "transcript_segments")
        segment_pk = cols["segment_id"]["pk"] if "segment_id" in cols else 0
        transcript_pk = cols["transcript_id"]["pk"] if "transcript_id" in cols else 0
        if "transcript_id" not in cols or not transcript_pk or segment_pk == 1:
            archive_name = "transcript_segments_legacy_unbound"
            if connection.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
                (archive_name,)
            ).fetchone():
                archive_name = "transcript_segments_legacy_unbound_2"
            connection.execute(f"ALTER TABLE transcript_segments RENAME TO {archive_name}")

    connection.execute("""
        CREATE TABLE IF NOT EXISTS transcript_segments (
            transcript_id TEXT NOT NULL REFERENCES transcripts(transcript_id) ON DELETE CASCADE,
            segment_id TEXT NOT NULL,
            start_seconds REAL NOT NULL CHECK(start_seconds >= 0),
            end_seconds REAL NOT NULL CHECK(end_seconds > start_seconds),
            text TEXT NOT NULL,
            words_json TEXT NOT NULL DEFAULT '[]',
            speaker TEXT,
            confidence REAL,
            created_at TEXT NOT NULL,
            PRIMARY KEY (transcript_id, segment_id)
        )
    """)
    connection.execute("""
        CREATE TABLE IF NOT EXISTS scene_plans (
            plan_id TEXT PRIMARY KEY,
            transcript_id TEXT NOT NULL REFERENCES transcripts(transcript_id) ON DELETE RESTRICT,
            audio_relative_path TEXT NOT NULL,
            audio_sha256 TEXT NOT NULL,
            duration_seconds REAL NOT NULL CHECK(duration_seconds > 0),
            plan_sha256 TEXT NOT NULL,
            plan_path TEXT NOT NULL,
            created_at TEXT NOT NULL
        )
    """)

    scene_cols = _columns(connection, "scenes")
    if scene_cols:
        if "plan_id" not in scene_cols:
            connection.execute("ALTER TABLE scenes ADD COLUMN plan_id TEXT")
        if "transcript_id" not in scene_cols:
            connection.execute("ALTER TABLE scenes ADD COLUMN transcript_id TEXT")

    # Asset review history: keep the reason, not only the current state.
    asset_cols = _columns(connection, "assets")
    if asset_cols:
        if "review_note" not in asset_cols:
            connection.execute("ALTER TABLE assets ADD COLUMN review_note TEXT")
        if "reviewed_at" not in asset_cols:
            connection.execute("ALTER TABLE assets ADD COLUMN reviewed_at TEXT")
    connection.execute("""
        CREATE TABLE IF NOT EXISTS scene_citations (
            scene_id TEXT NOT NULL REFERENCES scenes(scene_id) ON DELETE CASCADE,
            source_id TEXT NOT NULL REFERENCES sources(source_id) ON DELETE RESTRICT,
            PRIMARY KEY (scene_id, source_id)
        )
    """)
    connection.execute("""
        CREATE TABLE IF NOT EXISTS scene_transcript_segments (
            scene_id TEXT NOT NULL REFERENCES scenes(scene_id) ON DELETE CASCADE,
            transcript_id TEXT NOT NULL,
            segment_id TEXT NOT NULL,
            FOREIGN KEY (transcript_id, segment_id)
                REFERENCES transcript_segments(transcript_id, segment_id) ON DELETE RESTRICT,
            PRIMARY KEY (scene_id, transcript_id, segment_id)
        )
    """)
    connection.execute(
        "CREATE INDEX IF NOT EXISTS idx_transcript_segments_version_time "
        "ON transcript_segments(transcript_id, start_seconds, end_seconds)"
    )
    connection.execute("CREATE INDEX IF NOT EXISTS idx_scenes_plan ON scenes(plan_id)")
