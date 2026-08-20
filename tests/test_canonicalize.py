from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path

from cleargrad_course_api.canonicalize import canonicalize_courses

FIXTURE = Path(__file__).parent / "fixtures" / "courses.json"


def load_courses() -> list[dict]:
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


def test_exact_and_description_only_duplicates_are_safely_canonicalized() -> None:
    rows = load_courses()
    exact_copy = deepcopy(rows[0])
    richer_description = deepcopy(rows[0])
    richer_description["description"] += "，包含更完整的官方備註"
    canonical, stats = canonicalize_courses([*rows, exact_copy, richer_description])

    assert len(canonical) == len(rows)
    assert stats["exactDuplicatesRemoved"] == 1
    assert stats["descriptionVariantsMerged"] == 1
    assert stats["removedCourseCount"] == 2
    assert stats["unresolvedConflictGroupCount"] == 0
    assert canonical[0]["description"] == richer_description["description"]


def test_non_description_conflicts_are_preserved_for_review() -> None:
    rows = load_courses()
    conflicting = deepcopy(rows[0])
    conflicting["name"] = "同識別但不同名稱"
    canonical, stats = canonicalize_courses([rows[0], conflicting])

    assert len(canonical) == 2
    assert stats["removedCourseCount"] == 0
    assert stats["unresolvedConflictGroupCount"] == 1
    assert stats["unresolvedConflicts"][0]["differingFields"] == ["name"]

