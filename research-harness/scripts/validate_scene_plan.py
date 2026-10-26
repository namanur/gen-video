#!/usr/bin/env python3
"""Validate scene timing and asset references before Hyperframes rendering."""
from __future__ import annotations

import argparse
import json
import math
import posixpath
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

TIMING_TOLERANCE = 0.05


def _load_episode(root: Path) -> dict:
    return json.loads((root / "episode.json").read_text(encoding="utf-8"))


def _plan_reference(root: Path, plan_path: Path | None) -> str:
    if plan_path is None:
        return "edit/scene_plan.json"
    try:
        return plan_path.resolve().relative_to(root).as_posix()
    except ValueError:
        return plan_path.name


def validate_plan(root: str | Path, plan: object,
                  allow_candidate: bool = False) -> tuple[list[str], list[str], dict]:
    """Return errors, warnings, and a small summary. Never writes to the catalog."""
    root = Path(root).expanduser().resolve()
    errors: list[str] = []
    warnings: list[str] = []

    if not isinstance(plan, dict) or not isinstance(plan.get("scenes"), list):
        return ["scene plan must be an object with a scenes array"], warnings, {}
    if not plan["scenes"]:
        return ["scene plan must contain at least one scene"], warnings, {}
    db_path = root / "episode.sqlite3"
    if not db_path.is_file():
        return [f"episode database not found: {db_path}"], warnings, {}

    try:
        episode = _load_episode(root)
    except (OSError, json.JSONDecodeError) as error:
        errors.append(f"cannot read episode.json: {error}")
        episode = {}
    if plan.get("project_slug") != episode.get("slug"):
        errors.append("project_slug does not match episode.json slug")

    canvas = plan.get("canvas")
    if not isinstance(canvas, dict):
        errors.append("canvas must be an object")
    else:
        for key in ("width", "height", "fps"):
            value = canvas.get(key)
            if isinstance(value, bool) or not isinstance(value, (int, float)) or value <= 0:
                errors.append(f"canvas.{key} must be a positive number")
            elif not math.isfinite(float(value)):
                errors.append(f"canvas.{key} must be finite")

    transcript = None
    segments: dict[str, sqlite3.Row] = {}
    with sqlite3.connect(db_path) as connection:
        connection.row_factory = sqlite3.Row
        has_transcripts = connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='transcripts'"
        ).fetchone()
        if not has_transcripts:
            errors.append(
                "episode database predates transcript versioning; recreate it with init_episode.py"
            )
        else:
            current = connection.execute(
                "SELECT value FROM project_meta WHERE key = 'current_transcript_id'"
            ).fetchone()
            if current is None:
                errors.append("no current transcript; import timed narration before validating scenes")
            else:
                transcript = connection.execute(
                    "SELECT * FROM transcripts WHERE transcript_id = ?", (current["value"],)
                ).fetchone()
                if transcript is None:
                    errors.append(
                        f"current transcript {current['value']} is missing from the transcripts table"
                    )
                else:
                    rows = connection.execute(
                        "SELECT segment_id, start_seconds, end_seconds FROM transcript_segments "
                        "WHERE transcript_id = ? ORDER BY start_seconds",
                        (transcript["transcript_id"],)
                    ).fetchall()
                    segments = {row["segment_id"]: row for row in rows}
                    if not segments:
                        errors.append("current transcript has no segments")

        source_ids = {
            row["source_id"] for row in connection.execute("SELECT source_id FROM sources")
        }

        referenced_assets: set[str] = set()
        for scene in plan["scenes"]:
            if not isinstance(scene, dict):
                continue
            assets = scene.get("asset_ids", [])
            if isinstance(assets, list):
                referenced_assets.update(item for item in assets if isinstance(item, str) and item)
        assets: dict[str, sqlite3.Row] = {}
        if referenced_assets:
            placeholders = ",".join("?" for _ in referenced_assets)
            rows = connection.execute(
                "SELECT asset_id, relative_path, review_status FROM assets "
                f"WHERE asset_id IN ({placeholders})",
                tuple(sorted(referenced_assets))
            ).fetchall()
            assets = {row["asset_id"]: row for row in rows}

    effective_duration: float | None = None
    audio = plan.get("audio")
    if not isinstance(audio, dict):
        errors.append("audio must be an object")
    else:
        plan_transcript_id = audio.get("transcript_id")
        if not isinstance(plan_transcript_id, str) or not plan_transcript_id.strip():
            errors.append("audio.transcript_id is required")
        elif transcript is not None and plan_transcript_id != transcript["transcript_id"]:
            errors.append(
                f"audio.transcript_id {plan_transcript_id} is not the current transcript "
                f"{transcript['transcript_id']}"
            )

        audio_path = audio.get("path")
        if not isinstance(audio_path, str) or not audio_path.strip():
            errors.append("audio.path is required")
        elif transcript is not None:
            normalized_path = posixpath.normpath(audio_path.replace("\\", "/"))
            if normalized_path != transcript["audio_path"]:
                errors.append(
                    f"audio.path {audio_path} does not match the current transcript audio "
                    f"{transcript['audio_path']}"
                )

        audio_hash = audio.get("sha256")
        if not isinstance(audio_hash, str) or len(audio_hash) != 64:
            errors.append("audio.sha256 must be the 64-character SHA-256 of the narration file")
        elif transcript is not None and audio_hash.lower() != transcript["audio_sha256"]:
            errors.append(
                "audio.sha256 does not match the current transcript audio; the scene plan is stale"
            )

        try:
            plan_duration = float(audio["duration_seconds"])
        except (KeyError, TypeError, ValueError):
            errors.append("audio.duration_seconds must be a positive number")
        else:
            if plan_duration <= 0:
                errors.append("audio.duration_seconds must be a positive number")
            elif not math.isfinite(plan_duration):
                errors.append("audio.duration_seconds must be finite")
            elif transcript is not None:
                tolerance = max(0.05, min(0.5, transcript["duration_seconds"] * 0.001))
                if abs(plan_duration - transcript["duration_seconds"]) > tolerance:
                    errors.append(
                        f"audio.duration_seconds {plan_duration} does not match the current "
                        f"transcript duration {transcript['duration_seconds']}"
                    )
                effective_duration = transcript["duration_seconds"]
            else:
                effective_duration = plan_duration

    previous_start = -1.0
    previous_end = 0.0
    seen_scene_ids: set[str] = set()
    referenced_segments: set[str] = set()
    for index, scene in enumerate(plan["scenes"]):
        prefix = f"scene[{index}]"
        if not isinstance(scene, dict):
            errors.append(f"{prefix} must be an object")
            continue
        scene_id = str(scene.get("scene_id") or "").strip()
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
            continue
        if not math.isfinite(start) or not math.isfinite(end):
            errors.append(f"{prefix} has non-finite timings")
            continue
        if start < previous_start:
            errors.append(f"{prefix} is out of chronological order")
        if start < previous_end - 0.02 and not scene.get("allow_overlap", False):
            errors.append(f"{prefix} overlaps the prior scene without allow_overlap=true")
        if effective_duration is not None and end > effective_duration + TIMING_TOLERANCE:
            errors.append(f"{prefix} ends after narration duration ({effective_duration}s)")

        if not str(scene.get("visual_goal", "")).strip():
            errors.append(f"{prefix} needs a visual_goal")

        segment_ids = scene.get("transcript_segment_ids")
        if not isinstance(segment_ids, list) or not segment_ids:
            errors.append(f"{prefix} needs a non-empty transcript_segment_ids array")
            segment_ids = []
        elif all(isinstance(item, str) for item in segment_ids) and len(segment_ids) != len(set(segment_ids)):
            errors.append(f"{prefix} contains duplicate transcript segment IDs")
        for segment_id in segment_ids:
            if not isinstance(segment_id, str) or not segment_id:
                errors.append(f"{prefix} contains an invalid transcript segment ID")
                continue
            if transcript is None:
                continue
            segment = segments.get(segment_id)
            if segment is None:
                errors.append(f"{prefix} references unknown transcript segment {segment_id}")
                continue
            referenced_segments.add(segment_id)
            if end <= segment["start_seconds"] + 0.02 or start >= segment["end_seconds"] - 0.02:
                errors.append(
                    f"{prefix} does not overlap transcript segment {segment_id} "
                    f"({segment['start_seconds']}..{segment['end_seconds']})"
                )

        asset_ids = scene.get("asset_ids", [])
        if not isinstance(asset_ids, list):
            errors.append(f"{prefix} asset_ids must be an array")
            asset_ids = []
        elif all(isinstance(item, str) for item in asset_ids) and len(asset_ids) != len(set(asset_ids)):
            errors.append(f"{prefix} contains duplicate asset IDs")
        graphic_spec = scene.get("graphic_spec")
        if not asset_ids and not graphic_spec:
            errors.append(f"{prefix} needs asset_ids or an explicit graphic_spec")
        if graphic_spec is not None:
            if not isinstance(graphic_spec, dict):
                errors.append(f"{prefix} graphic_spec must be an object")
            elif not str(graphic_spec.get("description", "")).strip() or not str(graphic_spec.get("type", "")).strip():
                errors.append(f"{prefix} graphic_spec needs type and description")
        for asset_id in asset_ids:
            if not isinstance(asset_id, str) or not asset_id:
                errors.append(f"{prefix} contains an invalid asset ID")
                continue
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
            allowed = ("approved", "candidate") if allow_candidate else ("approved",)
            if asset["review_status"] not in allowed:
                errors.append(
                    f"{prefix} asset {asset_id} is not render-approved ({asset['review_status']})"
                )

        citation_ids = scene.get("citation_source_ids", [])
        if not isinstance(citation_ids, list):
            errors.append(f"{prefix} citation_source_ids must be an array")
            citation_ids = []
        elif all(isinstance(item, str) for item in citation_ids) and len(citation_ids) != len(set(citation_ids)):
            errors.append(f"{prefix} contains duplicate citation source IDs")
        for source_id in citation_ids:
            if not isinstance(source_id, str) or not source_id:
                errors.append(f"{prefix} contains an invalid citation source ID")
            elif source_id not in source_ids:
                errors.append(f"{prefix} references unknown citation source_id {source_id}")

        on_screen_text = scene.get("on_screen_text", [])
        if not isinstance(on_screen_text, list) or any(not isinstance(item, str) for item in on_screen_text):
            errors.append(f"{prefix} on_screen_text must be an array of strings")

        previous_start, previous_end = start, max(previous_end, end)

    if transcript is not None and segments:
        unused = sorted(set(segments) - referenced_segments)
        if unused:
            preview = ", ".join(unused[:8])
            more = "" if len(unused) <= 8 else f" and {len(unused) - 8} more"
            warnings.append(
                f"{len(unused)} transcript segment(s) are not referenced by any scene: {preview}{more}"
            )

    summary = {
        "scenes": len(plan["scenes"]),
        "assets": len(referenced_assets),
        "segments": len(segments),
        "transcript_id": transcript["transcript_id"] if transcript is not None else None,
        "duration_seconds": effective_duration,
    }
    return errors, warnings, summary


def save_plan(root: str | Path, plan: dict, plan_path: Path | None = None) -> int:
    """Replace the stored scenes for this plan after validation has passed."""
    root = Path(root).expanduser().resolve()
    db_path = root / "episode.sqlite3"
    transcript_id = plan["audio"]["transcript_id"]
    scene_ids = [str(scene["scene_id"]) for scene in plan["scenes"]]
    now = datetime.now(timezone.utc).isoformat()

    with sqlite3.connect(db_path) as connection:
        connection.execute("PRAGMA foreign_keys = ON")
        if scene_ids:
            placeholders = ",".join("?" for _ in scene_ids)
            connection.execute(f"DELETE FROM scenes WHERE scene_id IN ({placeholders})", scene_ids)
        for scene in plan["scenes"]:
            connection.execute(
                "INSERT INTO scenes(scene_id, transcript_id, start_seconds, end_seconds, "
                "narration_text, visual_goal, on_screen_text_json, citation_source_ids_json, "
                "motion, graphic_spec_json, status, notes) "
                "VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (str(scene["scene_id"]), transcript_id, float(scene["start_seconds"]),
                 float(scene["end_seconds"]), scene.get("narration_text"),
                 scene.get("visual_goal"), json.dumps(scene.get("on_screen_text", []), ensure_ascii=False),
                 json.dumps(scene.get("citation_source_ids", []), ensure_ascii=False),
                 scene.get("motion"),
                 json.dumps(scene["graphic_spec"], ensure_ascii=False) if scene.get("graphic_spec") else None,
                 "validated", scene.get("notes"))
            )
            for asset_id in scene.get("asset_ids", []):
                connection.execute(
                    "INSERT INTO scene_assets(scene_id, asset_id, role, timing_json) "
                    "VALUES(?, ?, 'primary', '{}')", (str(scene["scene_id"]), asset_id)
                )
            for segment_id in scene.get("transcript_segment_ids", []):
                connection.execute(
                    "INSERT INTO scene_segments(scene_id, transcript_id, segment_id) VALUES(?, ?, ?)",
                    (str(scene["scene_id"]), transcript_id, segment_id)
                )
            for source_id in scene.get("citation_source_ids", []):
                connection.execute(
                    "INSERT INTO scene_sources(scene_id, source_id) VALUES(?, ?)",
                    (str(scene["scene_id"]), source_id)
                )
        reference = _plan_reference(root, plan_path)
        connection.execute(
            "INSERT OR REPLACE INTO project_meta(key, value) VALUES('current_scene_plan', ?)",
            (reference,)
        )
        connection.execute(
            "INSERT INTO activity_log(created_at, actor, action, entity_type, entity_id, details_json) "
            "VALUES(?, ?, ?, ?, ?, ?)",
            (now, "validate_scene_plan.py", "scene_plan_saved", "scene_plan", reference,
             json.dumps({"scenes": len(plan["scenes"]), "transcript_id": transcript_id},
                        ensure_ascii=False))
        )
    return len(plan["scenes"])


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", required=True)
    parser.add_argument("--plan", required=True)
    parser.add_argument("--allow-candidate", action="store_true",
                        help="Allow candidate assets for private drafts; never final delivery")
    parser.add_argument("--save", action="store_true",
                        help="Persist validated scenes and their links in SQLite")
    args = parser.parse_args()

    root = Path(args.root).expanduser().resolve()
    plan_path = Path(args.plan).expanduser().resolve()
    if not (root / "episode.sqlite3").is_file():
        parser.error(f"episode database not found: {root / 'episode.sqlite3'}")
    if not plan_path.is_file():
        parser.error(f"scene plan not found: {plan_path}")
    try:
        plan = json.loads(plan_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as error:
        parser.error(f"invalid JSON: {error}")

    errors, warnings, summary = validate_plan(root, plan, allow_candidate=args.allow_candidate)
    for warning in warnings:
        print(f"Warning: {warning}")
    if errors:
        print(f"Scene plan NOT READY: {len(errors)} issue(s)")
        for issue in errors:
            print(f"- {issue}")
        return 1

    if args.save:
        try:
            saved = save_plan(root, plan, plan_path)
        except (sqlite3.Error, KeyError, TypeError, ValueError) as error:
            print(f"Scene plan validation passed, but saving failed: {error}")
            return 1
        print(f"Saved {saved} validated scenes and their asset/segment/source links.")

    print(
        f"Scene plan valid: {summary['scenes']} scenes, {summary['assets']} catalogued assets, "
        f"{summary['segments']} transcript segments."
    )
    print(f"Transcript: {summary['transcript_id']} | Narration duration: {summary['duration_seconds']}s")
    print("Validation checks structure, transcript/audio identity, and local files, not visual quality or copyright status.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
