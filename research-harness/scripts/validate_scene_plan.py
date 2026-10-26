#!/usr/bin/env python3
"""Validate and persist a scene plan against the exact audio, transcript, sources, and assets."""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _finite_number(value: Any, label: str) -> float:
    if isinstance(value, bool):
        raise ValueError(f"{label} must be numeric")
    try:
        parsed = float(value)
    except (TypeError, ValueError) as error:
        raise ValueError(f"{label} must be numeric") from error
    if not math.isfinite(parsed):
        raise ValueError(f"{label} must be finite")
    return parsed


def validate_scene_plan(
    root: Path,
    plan_path: Path,
    *,
    allow_candidate: bool = False,
    persist: bool = True,
) -> tuple[list[str], int, int]:
    root = root.expanduser().resolve()
    plan_path = plan_path.expanduser().resolve()
    db_path = root / "episode.sqlite3"
    if not db_path.is_file():
        raise ValueError(f"episode database not found: {db_path}")
    if not plan_path.is_file():
        raise ValueError(f"scene plan not found: {plan_path}")
    try:
        plan = json.loads(plan_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as error:
        raise ValueError(f"invalid JSON: {error}") from error
    if not isinstance(plan, dict) or not isinstance(plan.get("scenes"), list):
        raise ValueError("scene plan must be an object with a scenes array")

    errors: list[str] = []
    if not plan["scenes"]:
        errors.append("scenes array must contain at least one scene")
    plan_id = plan.get("plan_id")
    transcript_id = plan.get("transcript_id")
    if not isinstance(plan_id, str) or not plan_id.strip():
        errors.append("plan_id must be a non-empty string")
    if not isinstance(transcript_id, str) or not transcript_id.strip():
        errors.append("transcript_id must identify an imported transcript")

    try:
        episode = json.loads((root / "episode.json").read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise ValueError(f"cannot read episode.json: {error}") from error
    if plan.get("project_slug") != episode.get("slug"):
        errors.append("project_slug does not match episode.json slug")

    audio = plan.get("audio")
    if not isinstance(audio, dict):
        audio = {}
        errors.append("audio must be an object")
    audio_path_value = audio.get("path")
    audio_sha = audio.get("sha256")
    try:
        duration = _finite_number(audio.get("duration_seconds"), "audio.duration_seconds")
        if duration <= 0:
            errors.append("audio.duration_seconds must be positive")
    except ValueError as error:
        errors.append(str(error))
        duration = 0.0
    if not isinstance(audio_path_value, str) or not audio_path_value.strip():
        errors.append("audio.path must identify the narration file relative to the episode root")
        audio_relative_path = ""
        resolved_audio_path = None
    else:
        resolved_audio_path = (root / audio_path_value).resolve()
        try:
            audio_relative_path = resolved_audio_path.relative_to(root).as_posix()
        except ValueError:
            errors.append("audio.path escapes the episode root")
            audio_relative_path = ""
            resolved_audio_path = None
    if not isinstance(audio_sha, str) or len(audio_sha) != 64:
        errors.append("audio.sha256 must contain the SHA-256 hex digest of the exact audio file")
        audio_sha = ""
    actual_audio_sha = None
    if resolved_audio_path is not None:
        if not resolved_audio_path.is_file():
            errors.append(f"narration audio file is missing: {audio_relative_path}")
        else:
            actual_audio_sha = sha256_file(resolved_audio_path)
            if actual_audio_sha.casefold() != audio_sha.casefold():
                errors.append("audio.sha256 does not match the current narration file")

    # Migrate older pilot databases before querying versioned contract tables.
    with sqlite3.connect(db_path) as connection:
        from db_contracts import ensure_contract_schema
        ensure_contract_schema(connection)
        connection.row_factory = sqlite3.Row

        transcript = None
        transcript_segments: dict[str, sqlite3.Row] = {}
        if isinstance(transcript_id, str) and transcript_id.strip():
            transcript = connection.execute(
                "SELECT transcript_id, audio_relative_path, audio_sha256, duration_seconds "
                "FROM transcripts WHERE transcript_id=?",
                (transcript_id,)
            ).fetchone()
            if transcript is None:
                errors.append(f"unknown transcript_id: {transcript_id}")
            else:
                if transcript["audio_relative_path"] != audio_relative_path:
                    errors.append("plan audio.path does not match the transcript's registered audio path")
                if str(transcript["audio_sha256"]).casefold() != str(audio_sha).casefold():
                    errors.append("plan audio.sha256 does not match the transcript's registered audio hash")
                if abs(float(transcript["duration_seconds"]) - duration) > 0.05:
                    errors.append("plan audio.duration_seconds does not match the transcript duration")
                transcript_segments = {
                    row["segment_id"]: row for row in connection.execute(
                        "SELECT segment_id, start_seconds, end_seconds, text FROM transcript_segments "
                        "WHERE transcript_id=?",
                        (transcript_id,)
                    ).fetchall()
                }

        scene_list = plan["scenes"]
        asset_ids = {
            asset_id
            for scene in scene_list if isinstance(scene, dict)
            for asset_id in scene.get("asset_ids", [])
            if isinstance(asset_id, str)
        }
        assets: dict[str, sqlite3.Row] = {}
        if asset_ids:
            placeholders = ",".join("?" for _ in asset_ids)
            rows = connection.execute(
                f"SELECT asset_id, relative_path, review_status FROM assets WHERE asset_id IN ({placeholders})",
                tuple(sorted(asset_ids))
            ).fetchall()
            assets = {row["asset_id"]: row for row in rows}

        source_ids = {
            source_id
            for scene in scene_list if isinstance(scene, dict)
            for source_id in scene.get("citation_source_ids", [])
            if isinstance(source_id, str)
        }
        sources: set[str] = set()
        if source_ids:
            placeholders = ",".join("?" for _ in source_ids)
            sources = {
                row[0] for row in connection.execute(
                    f"SELECT source_id FROM sources WHERE source_id IN ({placeholders})",
                    tuple(sorted(source_ids))
                ).fetchall()
            }

        previous_start = -1.0
        previous_end = 0.0
        seen_scene_ids: set[str] = set()
        normalized_scenes: list[dict[str, Any]] = []
        allowed_states = ("approved", "candidate") if allow_candidate else ("approved",)

        for index, scene in enumerate(scene_list):
            prefix = f"scene[{index}]"
            if not isinstance(scene, dict):
                errors.append(f"{prefix} must be an object")
                continue
            scene_id = scene.get("scene_id")
            if not isinstance(scene_id, str) or not scene_id.strip():
                errors.append(f"{prefix} is missing a non-empty scene_id")
                scene_id = f"__invalid_scene_{index}"
            elif scene_id in seen_scene_ids:
                errors.append(f"duplicate scene_id: {scene_id}")
            seen_scene_ids.add(scene_id)

            try:
                start = _finite_number(scene.get("start_seconds"), f"{prefix}.start_seconds")
                end = _finite_number(scene.get("end_seconds"), f"{prefix}.end_seconds")
            except ValueError as error:
                errors.append(str(error))
                continue
            if start < 0 or end <= start:
                errors.append(f"{prefix} has invalid range {start}..{end}")
            if start < previous_start:
                errors.append(f"{prefix} is out of chronological order")
            if start < previous_end - 0.02 and not scene.get("allow_overlap", False):
                errors.append(f"{prefix} overlaps the prior scene without allow_overlap=true")
            if duration > 0 and end > duration + 0.05:
                errors.append(f"{prefix} ends after narration duration ({duration}s)")
            if not isinstance(scene.get("visual_goal"), str) or not scene["visual_goal"].strip():
                errors.append(f"{prefix} needs a visual_goal")

            referenced = scene.get("asset_ids", [])
            if not isinstance(referenced, list):
                errors.append(f"{prefix} asset_ids must be an array")
                referenced = []
            if len(referenced) != len(set(item for item in referenced if isinstance(item, str))):
                errors.append(f"{prefix} contains duplicate asset IDs")
            if not referenced and not scene.get("graphic_spec"):
                errors.append(f"{prefix} needs asset_ids or an explicit graphic_spec")
            for asset_id in referenced:
                if not isinstance(asset_id, str):
                    errors.append(f"{prefix} asset_ids entries must be strings")
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
                if asset["review_status"] not in allowed_states:
                    errors.append(f"{prefix} asset {asset_id} is not render-approved ({asset['review_status']})")

            segment_ids = scene.get("transcript_segment_ids", [])
            if not isinstance(segment_ids, list):
                errors.append(f"{prefix} transcript_segment_ids must be an array")
                segment_ids = []
            if len(segment_ids) != len(set(item for item in segment_ids if isinstance(item, str))):
                errors.append(f"{prefix} contains duplicate transcript segment IDs")
            for segment_id in segment_ids:
                if not isinstance(segment_id, str):
                    errors.append(f"{prefix} transcript segment IDs must be strings")
                    continue
                segment = transcript_segments.get(segment_id)
                if segment is None:
                    errors.append(f"{prefix} references unknown transcript segment {segment_id}")
                    continue
                if start > float(segment["start_seconds"]) + 0.05 or end < float(segment["end_seconds"]) - 0.05:
                    errors.append(
                        f"{prefix} timing {start}..{end} does not cover transcript segment "
                        f"{segment_id} ({segment['start_seconds']}..{segment['end_seconds']})"
                    )

            citation_ids = scene.get("citation_source_ids", [])
            if not isinstance(citation_ids, list):
                errors.append(f"{prefix} citation_source_ids must be an array")
                citation_ids = []
            for source_id in citation_ids:
                if not isinstance(source_id, str):
                    errors.append(f"{prefix} citation_source_ids entries must be strings")
                elif source_id not in sources:
                    errors.append(f"{prefix} references unknown citation source_id {source_id}")

            on_screen_text = scene.get("on_screen_text", [])
            if not isinstance(on_screen_text, list) or not all(isinstance(x, str) for x in on_screen_text):
                errors.append(f"{prefix} on_screen_text must be an array of strings")
                on_screen_text = []
            previous_start, previous_end = start, max(previous_end, end)
            normalized_scenes.append({
                "scene_id": scene_id, "start_seconds": start, "end_seconds": end,
                "narration_text": scene.get("narration_text"),
                "visual_goal": str(scene.get("visual_goal", "")),
                "on_screen_text": on_screen_text,
                "asset_ids": [x for x in referenced if isinstance(x, str)],
                "transcript_segment_ids": [x for x in segment_ids if isinstance(x, str)],
                "citation_source_ids": [x for x in citation_ids if isinstance(x, str)],
                "motion": scene.get("motion"),
                "notes": scene.get("notes"),
                "graphic_spec": scene.get("graphic_spec"),
                "asset_roles": scene.get("asset_roles", {}),
                "asset_timings": scene.get("asset_timings", {}),
            })

        if isinstance(plan_id, str) and plan_id.strip() and seen_scene_ids:
            existing_ids = [x for x in seen_scene_ids if not x.startswith("__invalid_scene_")]
            if existing_ids:
                placeholders = ",".join("?" for _ in existing_ids)
                rows = connection.execute(
                    f"SELECT scene_id, plan_id FROM scenes WHERE scene_id IN ({placeholders})",
                    tuple(existing_ids)
                ).fetchall()
                for row in rows:
                    if row["plan_id"] != plan_id:
                        errors.append(
                            f"scene_id {row['scene_id']} already exists outside plan {plan_id} "
                            f"(current owner: {row['plan_id'] or 'legacy/unassigned'})"
                        )

        if not errors and persist:
            try:
                plan_relative_path = plan_path.relative_to(root).as_posix()
            except ValueError:
                errors.append("scene plan file must be stored inside the episode root")
            if not errors:
                now = datetime.now(timezone.utc).isoformat()
                plan_sha = sha256_file(plan_path)
                with connection:
                    connection.execute(
                        "INSERT INTO scene_plans(plan_id, transcript_id, audio_relative_path, audio_sha256, "
                        "duration_seconds, plan_sha256, plan_path, created_at) VALUES(?, ?, ?, ?, ?, ?, ?, ?) "
                        "ON CONFLICT(plan_id) DO UPDATE SET transcript_id=excluded.transcript_id, "
                        "audio_relative_path=excluded.audio_relative_path, audio_sha256=excluded.audio_sha256, "
                        "duration_seconds=excluded.duration_seconds, plan_sha256=excluded.plan_sha256, "
                        "plan_path=excluded.plan_path, created_at=excluded.created_at",
                        (plan_id, transcript_id, audio_relative_path, audio_sha, duration, plan_sha,
                         plan_relative_path, now)
                    )
                    connection.execute("DELETE FROM scenes WHERE plan_id=?", (plan_id,))
                    for scene in normalized_scenes:
                        scene_notes = json.dumps({
                            "notes": scene["notes"], "motion": scene["motion"],
                            "graphic_spec": scene["graphic_spec"],
                        }, ensure_ascii=False)
                        connection.execute(
                            "INSERT INTO scenes(scene_id, start_seconds, end_seconds, narration_text, visual_goal, "
                            "on_screen_text_json, status, notes, plan_id, transcript_id) "
                            "VALUES(?, ?, ?, ?, ?, ?, 'validated', ?, ?, ?)",
                            (scene["scene_id"], scene["start_seconds"], scene["end_seconds"],
                             scene["narration_text"], scene["visual_goal"],
                             json.dumps(scene["on_screen_text"], ensure_ascii=False), scene_notes,
                             plan_id, transcript_id)
                        )
                        for asset_id in scene["asset_ids"]:
                            role = scene["asset_roles"].get(asset_id, "primary") if isinstance(scene["asset_roles"], dict) else "primary"
                            timing = scene["asset_timings"].get(asset_id, {}) if isinstance(scene["asset_timings"], dict) else {}
                            connection.execute(
                                "INSERT INTO scene_assets(scene_id, asset_id, role, timing_json) VALUES(?, ?, ?, ?)",
                                (scene["scene_id"], asset_id, str(role), json.dumps(timing, ensure_ascii=False))
                            )
                        for segment_id in scene["transcript_segment_ids"]:
                            connection.execute(
                                "INSERT INTO scene_transcript_segments(scene_id, transcript_id, segment_id) VALUES(?, ?, ?)",
                                (scene["scene_id"], transcript_id, segment_id)
                            )
                        for source_id in scene["citation_source_ids"]:
                            connection.execute(
                                "INSERT INTO scene_citations(scene_id, source_id) VALUES(?, ?)",
                                (scene["scene_id"], source_id)
                            )
                    connection.execute(
                        "INSERT INTO activity_log(created_at, actor, action, entity_type, entity_id, details_json) "
                        "VALUES(?, ?, ?, ?, ?, ?)",
                        (now, "validate_scene_plan.py", "scene_plan_validated_and_persisted",
                         "scene_plan", plan_id, json.dumps({
                             "scene_count": len(normalized_scenes), "transcript_id": transcript_id,
                             "audio_sha256": audio_sha, "plan_sha256": plan_sha,
                         }, ensure_ascii=False))
                    )
    return errors, len(plan["scenes"]), len(asset_ids)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", required=True)
    parser.add_argument("--plan", required=True)
    parser.add_argument("--allow-candidate", action="store_true",
                        help="Allow candidate assets for private drafts; never final delivery")
    parser.add_argument("--no-persist", action="store_true", help="Validate without writing scenes to SQLite")
    args = parser.parse_args()
    try:
        errors, scene_count, asset_count = validate_scene_plan(
            Path(args.root), Path(args.plan),
            allow_candidate=args.allow_candidate, persist=not args.no_persist,
        )
    except (OSError, ValueError, sqlite3.Error) as error:
        parser.error(str(error))
    if errors:
        print(f"Scene plan NOT READY: {len(errors)} issue(s)")
        for issue in errors:
            print(f"- {issue}")
        return 1
    action = "validated" if args.no_persist else "validated and persisted"
    print(f"Scene plan {action}: {scene_count} scenes, {asset_count} catalogued assets.")
    print("Validation covers media identity, transcript segments, source IDs, assets, and scene timing.")
    print("It does not assess visual quality, factual truth, or publication rights.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
