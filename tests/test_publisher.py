from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path

import pytest

from cleargrad_course_api.io import read_json, sha256_json
from cleargrad_course_api.publisher import FetchedSnapshot, PublicationRejected, publish_snapshot

FIXTURE = Path(__file__).parent / "fixtures" / "courses.json"
NOW = datetime(2026, 8, 20, 8, 30, 34, tzinfo=timezone.utc)


def courses() -> list[dict]:
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


def snapshot(version: str, rows: list[dict]) -> FetchedSnapshot:
    return FetchedSnapshot(
        semester="1151",
        source_version=version,
        source_version_time="2026-08-20T08:30:34Z",
        courses=rows,
        source_base="https://example.test/api",
        source_root={"latest": "1151", "history": {"1151": "115上"}},
    )


def short_semester_snapshot(rows: list[dict]) -> FetchedSnapshot:
    return FetchedSnapshot(
        semester="1143",
        source_version="20260623_185953",
        source_version_time="2026-06-23T18:59:53Z",
        courses=rows,
        source_base="https://example.test/api",
        source_root={"latest": "1151", "history": {"1143": "114暑期", "1151": "115上"}},
    )


def test_publish_creates_compatible_versioned_snapshot_and_lkg(tmp_path: Path) -> None:
    rows = courses()
    result = publish_snapshot(
        snapshot("20260820_083034", rows),
        tmp_path,
        minimum_course_count=3,
        now=NOW,
    )
    assert result.changed
    assert read_json(tmp_path / "1151" / "version.json")["latest"] == "20260820_083034"
    assert read_json(tmp_path / "1151" / "20260820_083034" / "all.json") == rows
    assert read_json(tmp_path / "lkg" / "1151" / "all.json") == rows
    manifest = read_json(tmp_path / "1151" / "20260820_083034" / "manifest.json")
    assert manifest["sha256"] == sha256_json(rows)
    assert manifest["rawSha256"] == sha256_json(rows)
    assert manifest["compatibilityProfile"] == "NSYSUCourseAPI-v1"


def test_publish_keeps_raw_rows_and_serves_canonical_rows(tmp_path: Path) -> None:
    rows = courses()
    raw_rows = [*rows, dict(rows[0])]
    publish_snapshot(
        snapshot("20260820_083034", raw_rows),
        tmp_path,
        minimum_course_count=3,
        now=NOW,
    )
    snapshot_dir = tmp_path / "1151" / "20260820_083034"
    assert read_json(snapshot_dir / "all.raw.json") == raw_rows
    assert read_json(snapshot_dir / "all.json") == rows
    manifest = read_json(snapshot_dir / "manifest.json")
    assert manifest["rawCourseCount"] == 4
    assert manifest["courseCount"] == 3
    assert manifest["canonicalization"]["exactDuplicatesRemoved"] == 1


def test_same_source_version_is_idempotent(tmp_path: Path) -> None:
    first = snapshot("20260820_083034", courses())
    publish_snapshot(first, tmp_path, minimum_course_count=3, now=NOW)
    result = publish_snapshot(first, tmp_path, minimum_course_count=3, now=NOW)
    assert not result.changed


def test_same_source_version_with_changed_content_is_rejected(tmp_path: Path) -> None:
    rows = courses()
    first = snapshot("20260820_083034", rows)
    publish_snapshot(first, tmp_path, minimum_course_count=3, now=NOW)
    changed_rows = courses()
    changed_rows[0]["teacher"] = "unexpected mutation"
    with pytest.raises(PublicationRejected, match="without a new source version"):
        publish_snapshot(
            snapshot("20260820_083034", changed_rows),
            tmp_path,
            minimum_course_count=3,
            now=NOW,
        )
    assert read_json(tmp_path / "lkg" / "1151" / "all.json") == rows


def test_rejected_snapshot_does_not_replace_last_known_good(tmp_path: Path) -> None:
    first_rows = courses()
    publish_snapshot(
        snapshot("20260820_083034", first_rows),
        tmp_path,
        minimum_course_count=3,
        now=NOW,
    )
    with pytest.raises(PublicationRejected):
        publish_snapshot(
            snapshot("20260820_090000", first_rows[:1]),
            tmp_path,
            minimum_course_count=1,
            max_drop_ratio=0.10,
            now=NOW,
        )
    assert read_json(tmp_path / "1151" / "version.json")["latest"] == "20260820_083034"
    assert read_json(tmp_path / "lkg" / "1151" / "all.json") == first_rows


def test_short_third_semester_uses_adaptive_safety_floor(tmp_path: Path) -> None:
    seed = courses()[0]
    rows = []
    for index in range(10):
        row = dict(seed)
        row["id"] = f"SUMMER{index:02d}"
        rows.append(row)
    result = publish_snapshot(short_semester_snapshot(rows), tmp_path, now=NOW)
    assert result.course_count == 10


def test_short_third_semester_below_adaptive_floor_is_rejected(tmp_path: Path) -> None:
    seed = courses()[0]
    rows = []
    for index in range(9):
        row = dict(seed)
        row["id"] = f"SUMMER{index:02d}"
        rows.append(row)
    with pytest.raises(PublicationRejected, match="below the safety floor 10"):
        publish_snapshot(short_semester_snapshot(rows), tmp_path, now=NOW)
