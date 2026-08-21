from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from cleargrad_course_api.io import read_json, sha256_json
from cleargrad_course_api.publisher import (
    HYDRATION_CRITICAL_ATTEMPTS,
    FetchedSnapshot,
    PublicationRejected,
    audit_site,
    hydrate_site,
    publish_snapshot,
)

FIXTURE = Path(__file__).parent / "fixtures" / "courses.json"
NOW = datetime(2026, 8, 20, 8, 30, 34, tzinfo=UTC)


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
        source_name="NSYSUOfficial",
        source_url="https://example.test/official",
    )


def short_semester_snapshot(rows: list[dict]) -> FetchedSnapshot:
    return FetchedSnapshot(
        semester="1143",
        source_version="20260623_185953",
        source_version_time="2026-06-23T18:59:53Z",
        courses=rows,
        source_base="https://example.test/api",
        source_root={"latest": "1151", "history": {"1143": "114暑期", "1151": "115上"}},
        source_name="NSYSUOfficial",
        source_url="https://example.test/official",
    )


def fallback_snapshot(version: str, rows: list[dict]) -> FetchedSnapshot:
    value = snapshot(version, rows)
    return FetchedSnapshot(
        **{
            **value.__dict__,
            "source_name": "NSYSUCourseAPI",
            "source_url": "https://example.test/mirror/all.json",
        }
    )


def expanded_courses(count: int, *, department: str = "一般學系") -> list[dict]:
    seed = courses()[0]
    rows: list[dict] = []
    for index in range(count):
        row = dict(seed)
        row["id"] = f"COURSE{index:04d}"
        row["department"] = department
        row["select"] = index + 1
        row["selected"] = index + 2
        row["remaining"] = 100 - index
        rows.append(row)
    return rows


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


def test_new_timestamp_with_identical_content_does_not_create_a_version(tmp_path: Path) -> None:
    rows = courses()
    publish_snapshot(snapshot("20260820_083034", rows), tmp_path, minimum_course_count=3, now=NOW)
    result = publish_snapshot(
        snapshot("20260820_084534", rows),
        tmp_path,
        minimum_course_count=3,
        now=NOW,
    )
    assert not result.changed
    assert result.version == "20260820_083034"
    assert result.ignored_reason == "content_unchanged"
    assert not (tmp_path / "1151" / "20260820_084534").exists()


def test_older_fallback_cannot_replace_newer_official_snapshot(tmp_path: Path) -> None:
    rows = courses()
    official = snapshot("20260820_090000", rows)
    official = FetchedSnapshot(
        **{**official.__dict__, "source_name": "NSYSUOfficial", "source_url": "https://example.test"}
    )
    publish_snapshot(official, tmp_path, minimum_course_count=3, now=NOW)
    official_manifest = read_json(tmp_path / "1151" / "20260820_090000" / "manifest.json")
    assert official_manifest["source"] == "NSYSUOfficial"
    assert official_manifest["sourceUrl"] == "https://example.test"
    older_rows = courses()
    older_rows[0]["select"] += 1
    result = publish_snapshot(
        fallback_snapshot("20260820_083034", older_rows),
        tmp_path,
        minimum_course_count=3,
        now=NOW,
    )
    assert not result.changed
    assert result.version == "20260820_090000"
    assert result.source_name == "NSYSUOfficial"
    assert result.ignored_reason == "out_of_order_candidate"
    assert read_json(tmp_path / "lkg" / "1151" / "all.json") == rows


def test_fallback_cannot_bootstrap_without_an_official_baseline(tmp_path: Path) -> None:
    with pytest.raises(PublicationRejected, match="cannot establish a semester baseline"):
        publish_snapshot(
            fallback_snapshot("20260820_083034", courses()),
            tmp_path,
            minimum_course_count=3,
            now=NOW,
        )
    assert not (tmp_path / "version.json").exists()


def test_fallback_missing_one_department_is_rejected_even_when_total_is_unchanged(
    tmp_path: Path,
) -> None:
    general = expanded_courses(200)
    sports = expanded_courses(40, department="運動健康（體育）")
    for index, row in enumerate(sports):
        row["id"] = f"SPORT{index:04d}"
    baseline = [*general, *sports]
    publish_snapshot(snapshot("20260820_090000", baseline), tmp_path, minimum_course_count=3, now=NOW)

    replacement = expanded_courses(20, department="新增單位")
    for index, row in enumerate(replacement):
        row["id"] = f"NEW{index:04d}"
    incomplete = [*general, *sports[:20], *replacement]
    reports = tmp_path / "reports"
    with pytest.raises(PublicationRejected, match="運動健康（體育）.*possible partial catalog"):
        publish_snapshot(
            fallback_snapshot("20260820_091500", incomplete),
            tmp_path,
            report_dir=reports,
            minimum_course_count=3,
            now=NOW,
        )
    assert read_json(tmp_path / "lkg" / "1151" / "all.json") == baseline
    rejection = read_json(reports / "last-rejected.json")
    assert rejection["source"] == "NSYSUCourseAPI"
    assert rejection["validation"]["stats"]["continuity"]["sourcePolicy"] == "strict_fallback"
    assert any("運動健康（體育）" in error for error in rejection["validation"]["errors"])


def test_fallback_enrollment_values_cannot_silently_collapse(tmp_path: Path) -> None:
    baseline = expanded_courses(40)
    publish_snapshot(snapshot("20260820_090000", baseline), tmp_path, minimum_course_count=3, now=NOW)
    incomplete = [dict(row, select=0, selected=0) for row in baseline]

    with pytest.raises(PublicationRejected, match="enrollment field 'select'.*collapsed"):
        publish_snapshot(
            fallback_snapshot("20260820_091500", incomplete),
            tmp_path,
            minimum_course_count=3,
            now=NOW,
        )
    assert read_json(tmp_path / "lkg" / "1151" / "all.json") == baseline


def test_official_baseline_remains_durable_across_fallback_updates(tmp_path: Path) -> None:
    baseline = expanded_courses(40)
    publish_snapshot(snapshot("20260820_090000", baseline), tmp_path, minimum_course_count=3, now=NOW)

    first_fallback = [dict(row) for row in baseline]
    first_fallback[0]["select"] += 1
    publish_snapshot(
        fallback_snapshot("20260820_091500", first_fallback),
        tmp_path,
        minimum_course_count=3,
        now=NOW,
    )
    second_fallback = [dict(row) for row in first_fallback]
    second_fallback[1]["selected"] += 1
    publish_snapshot(
        fallback_snapshot("20260820_093000", second_fallback),
        tmp_path,
        minimum_course_count=3,
        now=NOW,
    )

    assert read_json(tmp_path / "official-baseline" / "1151" / "all.json") == baseline
    assert read_json(tmp_path / "lkg" / "1151" / "all.json") == second_fallback


def test_complete_official_snapshot_may_remove_courses_from_one_department(
    tmp_path: Path,
) -> None:
    general = expanded_courses(200)
    sports = expanded_courses(40, department="運動健康（體育）")
    for index, row in enumerate(sports):
        row["id"] = f"SPORT{index:04d}"
    baseline = [*general, *sports]
    publish_snapshot(snapshot("20260820_090000", baseline), tmp_path, minimum_course_count=3, now=NOW)

    official_update = [*general, *sports[:20]]
    result = publish_snapshot(
        snapshot("20260820_091500", official_update),
        tmp_path,
        minimum_course_count=3,
        now=NOW,
    )
    assert result.changed
    assert read_json(tmp_path / "lkg" / "1151" / "all.json") == official_update
    manifest = read_json(tmp_path / "1151" / "20260820_091500" / "manifest.json")
    assert any("accepted as an official catalog change" in item for item in manifest["warnings"])


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


def test_hydrate_retries_critical_pages_files_and_preserves_history(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    rows = expanded_courses(10)
    checksum = sha256_json(rows)
    version = "20260820_083034"
    critical_calls: list[tuple[str, int]] = []

    def fake_fetch_json(url: str, *, timeout: float = 45.0, attempts: int = 3):
        del timeout
        critical_calls.append((url, attempts))
        if url.endswith("/1143/version.json"):
            return {"latest": version, "history": {version: "2026-08-20T08:30:34Z"}}
        return {"latest": "1143", "history": {"1143": "114暑期"}}

    def fake_fetch_bytes(url: str, *, timeout: float = 45.0, attempts: int = 3) -> bytes:
        del timeout
        if url.endswith(f"/1143/{version}/all.json"):
            critical_calls.append((url, attempts))
            return json.dumps(rows, ensure_ascii=False).encode()
        if url.endswith(f"/1143/{version}/manifest.json"):
            critical_calls.append((url, attempts))
            return json.dumps({"sha256": checksum}).encode()
        if url.endswith(".json"):
            return b"{}"
        return b""

    monkeypatch.setattr("cleargrad_course_api.publisher.fetch_json", fake_fetch_json)
    monkeypatch.setattr("cleargrad_course_api.publisher.fetch_bytes", fake_fetch_bytes)

    notices = hydrate_site("https://example.test/course-api", tmp_path)

    assert notices == []
    assert read_json(tmp_path / "version.json")["history"] == {"1143": "114暑期"}
    assert all(attempts == HYDRATION_CRITICAL_ATTEMPTS for _, attempts in critical_calls)
    assert audit_site(tmp_path)["ok"] is True
