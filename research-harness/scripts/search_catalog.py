#!/usr/bin/env python3
"""Search existing sources and assets before repeating research or capture."""
from __future__ import annotations

import argparse
import sqlite3
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", required=True)
    parser.add_argument("query", nargs="+", help="Words to search in titles, URLs, descriptions, tags, and paths")
    parser.add_argument("--limit", type=int, default=20)
    parser.add_argument("--type", choices=("all", "sources", "assets"), default="all")
    args = parser.parse_args()
    if args.limit < 1 or args.limit > 100:
        parser.error("--limit must be between 1 and 100")

    root = Path(args.root).expanduser().resolve()
    db_path = root / "episode.sqlite3"
    if not db_path.is_file():
        parser.error(f"episode database not found: {db_path}")
    terms = [item.strip() for item in " ".join(args.query).split() if item.strip()]
    with sqlite3.connect(db_path) as connection:
        connection.row_factory = sqlite3.Row
        if args.type in ("all", "sources"):
            rows = connection.execute(
                "SELECT source_id, title, canonical_url, source_type, local_path, rights_status "
                "FROM sources ORDER BY accessed_at DESC"
            ).fetchall()
            matched = [row for row in rows if all(
                term.casefold() in " ".join(str(row[k] or "") for k in row.keys()).casefold()
                for term in terms
            )]
            for row in matched[:args.limit]:
                print(f"[SOURCE] {row['source_id']} | {row['title']}")
                print(f"  URL: {row['canonical_url']}")
                print(f"  type={row['source_type']} rights={row['rights_status']} local={row['local_path'] or 'no local copy'}")
            if not matched:
                print("[SOURCE] No matches")

        if args.type in ("all", "assets"):
            rows = connection.execute(
                "SELECT a.asset_id, a.asset_type, a.relative_path, a.description, a.origin_url, "
                "a.review_status, a.rights_status, GROUP_CONCAT(t.tag, ' ') AS tags "
                "FROM assets a LEFT JOIN asset_tags t ON t.asset_id=a.asset_id "
                "GROUP BY a.asset_id ORDER BY a.created_at DESC"
            ).fetchall()
            matched = [row for row in rows if all(
                term.casefold() in " ".join(str(row[k] or "") for k in row.keys()).casefold()
                for term in terms
            )]
            for row in matched[:args.limit]:
                print(f"[ASSET] {row['asset_id']} | {row['description']}")
                print(f"  path={row['relative_path']} type={row['asset_type']} tags={row['tags'] or '-'}")
                print(f"  review={row['review_status']} rights={row['rights_status']} origin={row['origin_url'] or '-'}")
            if not matched:
                print("[ASSET] No matches")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
