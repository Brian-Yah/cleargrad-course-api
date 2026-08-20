from __future__ import annotations

from collections import Counter
from dataclasses import asdict, dataclass, field
import json
from typing import Any

from .constants import (
    DEFAULT_MAX_DROP_RATIO,
    DEFAULT_MAX_DUPLICATE_RATIO,
    DEFAULT_MINIMUM_COURSE_COUNT,
    REQUIRED_FIELDS,
)


@dataclass
class ValidationReport:
    ok: bool
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    stats: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def section_key(row: dict[str, Any]) -> str:
    values = (
        row.get("id"),
        row.get("class"),
        row.get("department"),
        row.get("teacher"),
        row.get("room"),
        row.get("classTime"),
    )
    return json.dumps(values, ensure_ascii=False, separators=(",", ":"), sort_keys=True)


def _number(value: Any) -> float | None:
    if isinstance(value, bool) or value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def validate_courses(
    courses: Any,
    *,
    previous_courses: list[dict[str, Any]] | None = None,
    minimum_course_count: int = DEFAULT_MINIMUM_COURSE_COUNT,
    max_drop_ratio: float = DEFAULT_MAX_DROP_RATIO,
    max_duplicate_ratio: float = DEFAULT_MAX_DUPLICATE_RATIO,
) -> ValidationReport:
    errors: list[str] = []
    warnings: list[str] = []

    if not isinstance(courses, list):
        return ValidationReport(False, ["root must be a JSON array"], [], {"courseCount": 0})

    course_count = len(courses)
    if course_count < minimum_course_count:
        errors.append(
            f"course count {course_count} is below the safety floor {minimum_course_count}"
        )

    malformed_rows = 0
    invalid_time_rows = 0
    invalid_numeric_rows = 0
    empty_identity_rows = 0
    keys: list[str] = []

    for index, row in enumerate(courses):
        if not isinstance(row, dict):
            malformed_rows += 1
            continue
        missing = REQUIRED_FIELDS.difference(row)
        if missing:
            malformed_rows += 1
            if malformed_rows <= 5:
                errors.append(f"row {index} is missing fields: {', '.join(sorted(missing))}")
        if not str(row.get("id") or "").strip() or not str(row.get("name") or "").strip():
            empty_identity_rows += 1
        class_time = row.get("classTime")
        if not isinstance(class_time, list) or len(class_time) != 7:
            invalid_time_rows += 1
        if any(_number(row.get(field)) is None for field in ("credit", "restrict", "select", "selected", "remaining")):
            invalid_numeric_rows += 1
        keys.append(section_key(row))

    if malformed_rows > 5:
        errors.append(f"{malformed_rows} rows do not satisfy the compatibility field contract")
    if empty_identity_rows:
        errors.append(f"{empty_identity_rows} rows have an empty course id or name")
    if invalid_time_rows:
        errors.append(f"{invalid_time_rows} rows do not have exactly seven classTime values")
    if invalid_numeric_rows:
        errors.append(f"{invalid_numeric_rows} rows contain invalid numeric fields")

    duplicate_count = sum(count - 1 for count in Counter(keys).values() if count > 1)
    duplicate_ratio = duplicate_count / course_count if course_count else 0.0
    if duplicate_count:
        warnings.append(
            f"{duplicate_count} duplicate section identities detected; consumers should deduplicate"
        )
    if duplicate_ratio > max_duplicate_ratio:
        errors.append(
            f"duplicate section ratio {duplicate_ratio:.2%} exceeds {max_duplicate_ratio:.2%}"
        )

    previous_count = len(previous_courses or [])
    drop_ratio = 0.0
    if previous_count and course_count < previous_count:
        drop_ratio = (previous_count - course_count) / previous_count
        if drop_ratio > max_drop_ratio:
            errors.append(
                f"course count dropped {drop_ratio:.2%} from {previous_count} to {course_count}"
            )

    stats = {
        "courseCount": course_count,
        "previousCourseCount": previous_count or None,
        "dropRatio": round(drop_ratio, 6),
        "duplicateSectionCount": duplicate_count,
        "duplicateSectionRatio": round(duplicate_ratio, 6),
        "malformedRowCount": malformed_rows,
        "invalidTimeRowCount": invalid_time_rows,
        "invalidNumericRowCount": invalid_numeric_rows,
        "emptyIdentityRowCount": empty_identity_rows,
    }
    return ValidationReport(not errors, errors, warnings, stats)

