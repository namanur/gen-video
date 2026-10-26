#!/usr/bin/env python3
"""Register a research source and its bibliographic metadata in an episode catalog."""
from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urldefrag, urlparse


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", required=True)
    parser.add_argument("--source-id", required=True, help="Stable ID such as SRC-001")
    parser.add_argument("--url", required=True, help="Canonical URL")
    parser.add_argument("--title", required=True)
    parser.add_argument("--source-type", required=True,
                        choices=("paper", "book", "article", "official_docs", "dataset", "video", "interview", "other"))
    parser.add_argument("--author", default="")
    parser.add_argument("--publisher", default="")
    parser.add_argument("--published-date", default="")
    parser.add_argument("--local-path", default="", help="Optional saved local copy, relative to episode root")
    parser.add_argument("--quality", default="unassessed")
    parser.add_argument("--rights-status", default="unknown")
    parser.add_argument("--excerpt", default="")
    parser.add_argument("--notes", default="")
    args = parser.parse_args()

    root = Path(args.root).expanduser().resolve()
    db_path = root / "episode.sqlite3"
    if not db_path.is_file():
        parser.error(f"episode database not found: {db_path}")
    parsed = urlparse(args.url)
    if parsed.scheme not in ("http", "https") or not parsed.netloc:
        parser.error("--url must be a valid http(s) URL")
    canonical = urldefrag(args.url)[0]
    local_path = None
    digest = None
    if args.local_path:
        local = (root / args.local_path).resolve()
        try:
            local_path = local.relative_to(root).as_posix()
        except ValueError:
            parser.error("--local-path must stay inside episode root")
        if not local.is_file():
            parser.error(f"local source file not found: {local}")
        h = hashlib.sha256()
        with local.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                h.update(chunk)
        digest = h.hexdigest()

    now = datetime.now(timezone.utc).isoformat()
    with sqlite3.connect(db_path) as connection:
        connection.execute("PRAGMA foreign_keys = ON")
        existing = connection.execute(
            "SELECT source_id FROM sources WHERE canonical_url = ?", (canonical,)
        ).fetchone()
        if existing:
            print(f"Source already exists: {existing[0]} ({canonical})")
            return 0
        duplicate_id = connection.execute(
            "SELECT canonical_url FROM sources WHERE source_id = ?", (args.source_id,)
        ).fetchone()
        if duplicate_id:
            parser.error(f"source ID already used for {duplicate_id[0]}")
        connection.execute(
            "INSERT INTO sources(source_id, canonical_url, original_url, title, author, publisher, "
            "published_date, accessed_at, source_type, local_path, sha256, source_quality, rights_status, excerpt, notes) "
            "VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (args.source_id, canonical, args.url, args.title, args.author or None, args.publisher or None,
             args.published_date or None, now, args.source_type, local_path, digest, args.quality,
             args.rights_status, args.excerpt or None, args.notes or None)
        )
        connection.execute(
            "INSERT INTO activity_log(created_at, actor, action, entity_type, entity_id, details_json) "
            "VALUES(?, ?, ?, ?, ?, ?)",
            (now, "register_source.py", "source_registered", "source", args.source_id,
             json.dumps({"url": canonical, "local_path": local_path}, ensure_ascii=False))
        )
    print(f"Registered {args.source_id}: {args.title}")
    if local_path:
        print(f"Saved local copy: {local_path} (SHA-256 {digest})")
    else:
        print("No local copy attached. If this source needs an image/PDF/snapshot, save and register that asset separately.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
