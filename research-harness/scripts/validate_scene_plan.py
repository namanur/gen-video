#!/usr/bin/env python3
"""Validate scene timing and asset references before Hyperframes rendering."""
from __future__ import annotations

import argparse
import json
import sqlite3
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", required=True)
    parser.add_argument("--plan", required=True)
    parser.add_argument("--allow-candidate", action="store_true",
                        help="Allow candidate assets for private drafts; never final delivery")
    args = parser.parse_args()

    root = Path(args.root).expanduser().resolve()
    db_path = root / "episode.sqlite3"
    plan_path = Path(args.plan).expanduser().resolve()
    if not db_path.is_file():
        parser.error(f"episode database not found: {db_path}")
    if not plan_path.is_file():
        parser.error(f"scene plan not found: {plan_path}")
    try:
        plan = json.loads(plan_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as error:
        parser.error(f"invalid JSON: {error}")
    if not isinstance(plan, dict) or not isinstance(plan.get("scenes"), list):
        parser.error("scene plan must be an object with a scenes array")

    errors: list[str] = []
    audio = plan.get("audio", {})
    duration = audio.get("duration_seconds") if isinstance(audio, dict) else None
    if not isinstance(duration, (int, float)) or duration <= 0:
        errors.append("audio.duration_seconds must be a positive number")
    try:
        episode = json.loads((root / "episode.json").read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        parser.error(f"cannot read episode.json: {error}")
    if plan.get("project_slug") != episode.get("slug"):
        errors.append("project_slug does not match episode.json slug")

    asset_ids = {
        asset_id for scene in plan["scenes"] if isinstance(scene, dict)
        for asset_id in scene.get("asset_ids", []) if isinstance(asset_id, str)
    }
    with sqlite3.connect(db_path) as connection:
        connection.row_factory = sqlite3.Row
        assets = {}
        if asset_ids:
            placeholders = ",".join("?" for _ in asset_ids)
            rows = connection.execute(
                f"SELECT asset_id, relative_path, review_status FROM assets WHERE asset_id IN ({placeholders})",
                tuple(sorted(asset_ids))
            ).fetchall()
            assets = {row["asset_id"]: row for row in rows}

    previous_start = -1.0
    previous_end = 0.0
    seen_scene_ids: set[str] = set()
    for index, scene in enumerate(plan["scenes"]):
        prefix = f"scene[{index}]"
        if not isinstance(scene, dict):
            errors.append(f"{prefix} must be an object")
            continue
        scene_id = str(scene.get("scene_id", ""))
        if not scene_id:
            errors.append(f"{prefix} is missing scene_id")
        elif scene_id in seen_scene_ids:
            errors.append(f"duplicate scene_id: {scene_id}")
        seen_scene_ids.add(scene_id)
        try:
            start, end = float(scene["start_seconds"]), float(scene["end_seconds"])
        except (KeyError, TypeError, ValueError):
            errors.append(f"{prefix} needs numeric start_seconds and end_seconds")
            continue
        if start < 0 or end <= start:
            errors.append(f"{prefix} has invalid range {start}..{end}")
        if start < previous_start:
            errors.append(f"{prefix} is out of chronological order")
        if start < previous_end - 0.02 and not scene.get("allow_overlap", False):
            errors.append(f"{prefix} overlaps the prior scene without allow_overlap=true")
        if isinstance(duration, (int, float)) and end > duration + 0.05:
            errors.append(f"{prefix} ends after narration duration ({duration}s)")
        if not str(scene.get("visual_goal", "")).strip():
            errors.append(f"{prefix} needs a visual_goal")
        referenced = scene.get("asset_ids", [])
        if not isinstance(referenced, list):
            errors.append(f"{prefix} asset_ids must be an array")
            referenced = []
        if not referenced and not scene.get("graphic_spec"):
            errors.append(f"{prefix} needs asset_ids or an explicit graphic_spec")
        for asset_id in referenced:
            asset = assets.get(asset_id)
            if asset is None:
                errors.append(f"{prefix} references unknown asset_id {asset_id}")
                continue
            target = (root / asset["relative_path"]).resolve()
            try:
                target.relative_to(root)
            except ValueError:
                errors.append(f"{prefix} asset escapes episode root: {asset_id}")
                continue
            if not target.is_file():
                errors.append(f"{prefix} asset file is missing: {asset['relative_path']}")
            allowed = ("approved", "candidate") if args.allow_candidate else ("approved",)
            if asset["review_status"] not in allowed:
                errors.append(f"{prefix} asset {asset_id} is not render-approved ({asset['review_status']})")
        previous_start, previous_end = start, max(previous_end, end)

    if errors:
        print(f"Scene plan NOT READY: {len(errors)} issue(s)")
        for issue in errors:
            print(f"- {issue}")
        return 1
    print(f"Scene plan valid: {len(plan['scenes'])} scenes, {len(asset_ids)} catalogued assets.")
    print("Validation checks structure and local files, not visual quality or copyright status.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
