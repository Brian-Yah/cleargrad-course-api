from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path

import cleargrad_course_api.cli as cli
from cleargrad_course_api.direct import DirectCrawlError, OfficialCrawlResult
from cleargrad_course_api.io import read_json
from cleargrad_course_api.publisher import FetchedSnapshot, publish_snapshot


FIXTURE = Path(__file__).parent / "fixtures" / "courses.json"
NOW = datetime(2026, 8, 20, 9, 15, 0, tzinfo=timezone.utc)


def courses() -> list[dict]:
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


def test_sync_prefers_official_collector(monkeypatch, tmp_path: Path, capsys) -> None:
    monkeypatch.setattr(
        cli,
        "crawl_official_courses",
        lambda **_kwargs: OfficialCrawlResult(
            semester="1151",
            semester_history={"1151": "115暑碩"},
            courses=courses(),
            page_count=1,
            retrieved_at=NOW,
        ),
    )
    result = cli.main(
        [
            "sync",
            "--output",
            str(tmp_path / "site"),
            "--reports",
            str(tmp_path / "reports"),
            "--minimum-course-count",
            "3",
            "--no-backfill-missing",
        ]
    )
    assert result == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["source"] == "NSYSUOfficial"
    manifest = read_json(tmp_path / "site" / "1151" / payload["version"] / "manifest.json")
    assert manifest["source"] == "NSYSUOfficial"


def test_sync_falls_back_to_static_source(monkeypatch, tmp_path: Path, capsys) -> None:
    def fail_direct(**_kwargs):
        raise DirectCrawlError("campus site is temporarily slow")

    monkeypatch.setattr(cli, "crawl_official_courses", fail_direct)
    site = tmp_path / "site"
    baseline = courses()
    publish_snapshot(
        FetchedSnapshot(
            semester="1151",
            source_version="20260820_090000",
            source_version_time="2026-08-20T09:00:00Z",
            courses=baseline,
            source_base="https://example.test/official",
            source_root={"latest": "1151", "history": {"1151": "115暑碩"}},
            source_name="NSYSUOfficial",
            source_url="https://example.test/official/results",
        ),
        site,
        minimum_course_count=3,
        now=NOW,
    )
    fallback_courses = courses()
    fallback_courses[0]["select"] += 1
    monkeypatch.setattr(
        cli,
        "fetch_upstream_snapshot",
        lambda *_args, **_kwargs: FetchedSnapshot(
            semester="1151",
            source_version="20260820_091500",
            source_version_time="2026-08-20T09:15:00Z",
            courses=fallback_courses,
            source_base="https://example.test/api",
            source_root={"latest": "1151", "history": {"1151": "115暑碩"}},
        ),
    )
    result = cli.main(
        [
            "sync",
            "--output",
            str(site),
            "--reports",
            str(tmp_path / "reports"),
            "--minimum-course-count",
            "3",
            "--no-backfill-missing",
        ]
    )
    assert result == 0
    captured = capsys.readouterr()
    payload = json.loads(captured.out)
    assert payload["source"] == "NSYSUCourseAPI"
    assert payload["directCrawlFailure"] == "campus site is temporarily slow"
    assert "trying NSYSUCourseAPI fallback" in captured.err
