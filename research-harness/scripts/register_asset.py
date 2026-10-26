#!/usr/bin/env python3
"""Register a local evidence or media artifact in an episode's SQLite catalog."""
from __future__ import annotations

import argparse
import hashlib
import json
import mimetypes
import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path

ASSET_TYPES = (
    "screenshot", "image", "pdf", "article_snapshot", "chart", "diagram",
    "video_clip", "screen_recording", "audio", "book_excerpt", "other"
)
REVIEW_STATES = ("candidate", "approved", "rejected", "rights_review")
RIGHTS_STATES = (
    "creator_owned", "public_domain_verified", "CC0", "CC-BY", "CC-BY-SA",
    "licensed", "editorial_review", "fair_use_review", "restricted", "unknown"
)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", required=True)
    parser.add_argument("--path", required=True, help="Artifact path relative to episode root")
    parser.add_argument("--asset-type", required=True, choices=ASSET_TYPES)
    parser.add_argument("--description", required=True, help="Describe what is visible or useful")
    parser.add_argument("--source-id")
    parser.add_argument("--source-url")
    parser.add_argument("--capture-method", default="manual")
    parser.add_argument("--rights-status", default="unknown", choices=RIGHTS_STATES)
    parser.add_argument("--review-status", default="candidate", choices=REVIEW_STATES)
    parser.add_argument("--review-note", default="",
                        help="Reason for approval, rejection, or rights review")
    parser.add_argument("--tag", action="append", default=[])
    parser.add_argument("--metadata", default="{}", help="Optional JSON object")
    args = parser.parse_args()

    root = Path(args.root).expanduser().resolve()
    db_path = root / "episode.sqlite3"
    if not db_path.is_file():
        parser.error(f"episode database not found: {db_path}")
    artifact = (root / args.path).expanduser().resolve()
    try:
        relative = artifact.relative_to(root).as_posix()
    except ValueError:
        parser.error("asset path must stay inside the episode root")
    if not artifact.is_file():
        parser.error(f"artifact file does not exist: {artifact}")
    try:
        metadata = json.loads(args.metadata)
    except json.JSONDecodeError as error:
        parser.error(f"--metadata must be valid JSON: {error}")
    if not isinstance(metadata, dict):
        parser.error("--metadata must be a JSON object")

    digest = sha256_file(artifact)
    mime_type = mimetypes.guess_type(artifact.name)[0] or "application/octet-stream"
    asset_id = "A-" + uuid.uuid4().hex[:10].upper()
    now = datetime.now(timezone.utc).isoformat()

    with sqlite3.connect(db_path) as connection:
        connection.execute("PRAGMA foreign_keys = ON")
        if args.source_id:
            found = connection.execute(
                "SELECT 1 FROM sources WHERE source_id = ?", (args.source_id,)
            ).fetchone()
            if not found:
                parser.error(f"source_id does not exist in catalog: {args.source_id}")
        existing = connection.execute(
            "SELECT asset_id, relative_path FROM assets WHERE relative_path = ?", (relative,)
        ).fetchone()
        if existing:
            print(f"Already registered: {existing[0]} -> {existing[1]}")
            return 0
        same_hash = connection.execute(
            "SELECT asset_id, relative_path FROM assets WHERE sha256 = ? LIMIT 1", (digest,)
        ).fetchone()
        connection.execute(
            "INSERT INTO assets(asset_id, asset_type, relative_path, filename, description, origin_url, "
            "source_id, sha256, mime_type, capture_method, rights_status, review_status, review_note, "
            "reviewed_at, created_at, metadata_json) "
            "VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (asset_id, args.asset_type, relative, artifact.name, args.description, args.source_url,
             args.source_id, digest, mime_type, args.capture_method, args.rights_status,
             args.review_status, args.review_note or None,
             now if args.review_status != "candidate" or args.review_note else None,
             now, json.dumps(metadata, ensure_ascii=False))
        )
        for tag in sorted({item.strip().lower() for item in args.tag if item.strip()}):
            connection.execute(
                "INSERT OR IGNORE INTO asset_tags(asset_id, tag) VALUES(?, ?)", (asset_id, tag)
            )
        connection.execute(
            "INSERT INTO activity_log(created_at, actor, action, entity_type, entity_id, details_json) "
            "VALUES(?, ?, ?, ?, ?, ?)",
            (now, "register_asset.py", "asset_registered", "asset", asset_id,
             json.dumps({"path": relative, "sha256": digest, "review_status": args.review_status},
                        ensure_ascii=False))
        )
    print(f"Registered {asset_id}: {relative}")
    print(f"SHA-256: {digest}")
    print(f"MIME: {mime_type} | Review: {args.review_status} | Rights: {args.rights_status}")
    if same_hash:
        print(f"Note: identical bytes already exist as {same_hash[0]} at {same_hash[1]}; this record was retained.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
