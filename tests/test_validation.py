from __future__ import annotations

import json
from pathlib import Path

from cleargrad_course_api.validation import validate_courses

FIXTURE = Path(__file__).parent / "fixtures" / "courses.json"


def load_courses() -> list[dict]:
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


def test_valid_compatibility_rows_pass() -> None:
    report = validate_courses(load_courses(), minimum_course_count=3)
    assert report.ok
    assert report.stats["courseCount"] == 3


def test_missing_required_field_is_rejected() -> None:
    courses = load_courses()
    del courses[0]["classTime"]
    report = validate_courses(courses, minimum_course_count=3)
    assert not report.ok
    assert any("classTime" in error for error in report.errors)


def test_large_course_count_drop_is_rejected() -> None:
    previous = load_courses() * 10
    current = load_courses()
    report = validate_courses(
        current,
        previous_courses=previous,
        minimum_course_count=3,
        max_drop_ratio=0.10,
        max_duplicate_ratio=1.0,
    )
    assert not report.ok
    assert any("dropped" in error for error in report.errors)

