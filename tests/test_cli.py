from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path

import cleargrad_course_api.cli as cli
from cleargrad_course_api.direct import DirectCrawlError, OfficialCrawlResult
from cleargrad_course_api.io import FetchError, read_json
from cleargrad_course_api.publisher import FetchedSnapshot, publish_snapshot


FIXTURE = Path(__file__).parent / "fixtures" / "courses.json"
NOW = datetime(2026, 8, 20, 9, 15, 0, tzinfo=timezone.utc)


def courses() -> list[dict]:
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


def test_sync_prefers_official_collector(monkeypatch, tmp_path: Path, capsys) -> None:
    observed: dict[str, object] = {}

    def succeed_direct(**kwargs):
        observed.update(kwargs)
        kwargs["progress"]("fixture stage completed")
        return OfficialCrawlResult(
            semester="1151",
            semester_history={"0821": "82上", "1151": "115暑碩"},
            courses=courses(),
            page_count=1,
            retrieved_at=NOW,
        )

    monkeypatch.setattr(
        cli,
        "crawl_official_courses",
        succeed_direct,
    )
    monkeypatch.setattr(
        cli,
        "fetch_upstream_snapshot",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("official history must not trigger mirror backfill")
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
        ]
    )
    assert result == 0
    captured = capsys.readouterr()
    payload = json.loads(captured.out)
    assert payload["source"] == "NSYSUOfficial"
    assert payload["backfilled"] == []
    assert observed["max_duration"] == 720.0
    assert "official: fixture stage completed" in captured.err
    manifest = read_json(tmp_path / "site" / "1151" / payload["version"] / "manifest.json")
    assert manifest["source"] == "NSYSUOfficial"
    assert read_json(tmp_path / "site" / "version.json")["history"] == {
        "1151": "115暑碩"
    }


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

    def fetch_mirror(_source_bases, *, semester=None, **_kwargs):
        if semester is not None:
            raise FetchError("historical fixture unavailable")
        return FetchedSnapshot(
            semester="1151",
            source_version="20260820_091500",
            source_version_time="2026-08-20T09:15:00Z",
            courses=fallback_courses,
            source_base="https://example.test/api",
            source_root={
                "latest": "1151",
                "history": {"1142": "114上", "1151": "115暑碩"},
            },
        )

    monkeypatch.setattr(cli, "fetch_upstream_snapshot", fetch_mirror)
    result = cli.main(
        [
            "sync",
            "--output",
            str(site),
            "--reports",
            str(tmp_path / "reports"),
            "--minimum-course-count",
            "3",
        ]
    )
    assert result == 0
    captured = capsys.readouterr()
    payload = json.loads(captured.out)
    assert payload["source"] == "NSYSUCourseAPI"
    assert payload["backfilled"] == []
    assert payload["directCrawlFailure"] == "campus site is temporarily slow"
    assert "trying NSYSUCourseAPI fallback" in captured.err
    assert "historical backfill skipped for 1142" in captured.err
