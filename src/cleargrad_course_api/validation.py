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


FALLBACK_SOURCE = "NSYSUCourseAPI"
ENROLLMENT_FIELDS = ("restrict", "select", "selected", "remaining")


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


def continuity_key(row: dict[str, Any]) -> str:
    """Return an identity that remains stable when teachers, rooms, or times change."""
    values = (row.get("id"), row.get("class"), row.get("department"))
    return json.dumps(values, ensure_ascii=False, separators=(",", ":"))


def _missing_count(before: Counter[str], after: Counter[str]) -> int:
    return sum((before - after).values())


def _nonzero_counts(rows: list[dict[str, Any]]) -> dict[str, int]:
    return {
        field: sum(1 for row in rows if (_number(row.get(field)) or 0) != 0)
        for field in ENROLLMENT_FIELDS
    }


def validate_continuity(
    courses: list[dict[str, Any]],
    *,
    baseline_courses: list[dict[str, Any]],
    source_name: str,
) -> ValidationReport:
    """Reject structurally incomplete snapshots while retaining the previous LKG.

    The static mirror is intentionally treated as an untrusted candidate. Its
    tolerances are tighter because known mirror failures can preserve the total
    row count while omitting one department or enrollment-state values.
    """
    errors: list[str] = []
    warnings: list[str] = []
    if not baseline_courses:
        if source_name == FALLBACK_SOURCE:
            errors.append(
                "NSYSUCourseAPI fallback cannot establish a semester baseline; "
                "a complete official snapshot is required first"
            )
        return ValidationReport(not errors, errors, warnings, {"baselineCourseCount": 0})

    fallback = source_name == FALLBACK_SOURCE
    baseline_count = len(baseline_courses)
    current_count = len(courses)
    count_drop = max(baseline_count - current_count, 0)
    count_drop_ratio = count_drop / baseline_count if baseline_count else 0.0
    count_ratio_limit = 0.02 if fallback else 0.10
    if count_drop and count_drop_ratio > count_ratio_limit:
        errors.append(
            f"course count dropped {count_drop_ratio:.2%} from trusted baseline "
            f"{baseline_count} to {current_count}; limit is {count_ratio_limit:.2%}"
        )

    baseline_identities = Counter(continuity_key(row) for row in baseline_courses)
    current_identities = Counter(continuity_key(row) for row in courses)
    missing_identities = _missing_count(baseline_identities, current_identities)
    identity_drop_ratio = missing_identities / baseline_count if baseline_count else 0.0
    identity_ratio_limit = 0.02 if fallback else 0.10
    identity_absolute_floor = 5 if fallback else 20
    identity_suspicious = (
        (
            missing_identities >= 10
            or (
                missing_identities >= identity_absolute_floor
                and identity_drop_ratio > identity_ratio_limit
            )
        )
        if fallback
        else (
            missing_identities >= identity_absolute_floor
            and identity_drop_ratio > identity_ratio_limit
        )
    )
    if identity_suspicious:
        message = f"stable section identities dropped {identity_drop_ratio:.2%} ({missing_identities} of {baseline_count})"
        if fallback:
            errors.append(message + "; fallback limit is 10 missing sections or 2.00%")
        else:
            warnings.append(message + "; accepted because the complete official catalog is authoritative")

    baseline_departments = Counter(
        str(row.get("department") or "").strip() for row in baseline_courses
    )
    current_departments = Counter(
        str(row.get("department") or "").strip() for row in courses
    )
    department_ratio_limit = 0.15 if fallback else 0.40
    department_absolute_floor = 3 if fallback else 10
    department_size_floor = 8 if fallback else 15
    department_drops: list[dict[str, Any]] = []
    for department, before in baseline_departments.items():
        after = current_departments.get(department, 0)
        missing = max(before - after, 0)
        ratio = missing / before if before else 0.0
        if missing:
            department_drops.append(
                {
                    "department": department,
                    "before": before,
                    "after": after,
                    "missing": missing,
                    "dropRatio": round(ratio, 6),
                }
            )
        if (
            before >= department_size_floor
            and missing >= department_absolute_floor
            and ratio > department_ratio_limit
        ):
            message = (
                f"department {department!r} dropped {ratio:.2%} "
                f"({before} to {after})"
            )
            if fallback:
                errors.append(message + "; possible partial catalog")
            else:
                warnings.append(message + "; accepted as an official catalog change")

    def has_schedule(row: dict[str, Any]) -> bool:
        values = row.get("classTime")
        return isinstance(values, list) and any(str(value).strip() for value in values)

    baseline_schedule_count = sum(has_schedule(row) for row in baseline_courses)
    current_schedule_count = sum(has_schedule(row) for row in courses)
    missing_schedules = max(baseline_schedule_count - current_schedule_count, 0)
    schedule_drop_ratio = (
        missing_schedules / baseline_schedule_count if baseline_schedule_count else 0.0
    )
    schedule_limit = 0.05 if fallback else 0.15
    if missing_schedules >= 10 and schedule_drop_ratio > schedule_limit:
        errors.append(
            f"populated class-time coverage dropped {schedule_drop_ratio:.2%} "
            f"({baseline_schedule_count} to {current_schedule_count})"
        )

    baseline_nonzero = _nonzero_counts(baseline_courses)
    current_nonzero = _nonzero_counts(courses)
    dynamic_department_collapses: list[dict[str, Any]] = []
    if fallback:
        for field in ENROLLMENT_FIELDS:
            before = baseline_nonzero[field]
            after = current_nonzero[field]
            if before >= 20 and after < before * 0.25:
                errors.append(
                    f"fallback enrollment field {field!r} non-zero coverage collapsed "
                    f"from {before} to {after}"
                )

            baseline_by_department = Counter(
                str(row.get("department") or "").strip()
                for row in baseline_courses
                if (_number(row.get(field)) or 0) != 0
            )
            current_by_department = Counter(
                str(row.get("department") or "").strip()
                for row in courses
                if (_number(row.get(field)) or 0) != 0
            )
            for department, department_before in baseline_by_department.items():
                department_after = current_by_department.get(department, 0)
                missing = max(department_before - department_after, 0)
                if department_before >= 5 and missing >= 4 and department_after < department_before * 0.25:
                    collapse = {
                        "field": field,
                        "department": department,
                        "before": department_before,
                        "after": department_after,
                    }
                    dynamic_department_collapses.append(collapse)
                    errors.append(
                        f"fallback enrollment field {field!r} collapsed in department "
                        f"{department!r} ({department_before} to {department_after})"
                    )

    stats = {
        "baselineCourseCount": baseline_count,
        "courseCount": current_count,
        "courseCountDropRatio": round(count_drop_ratio, 6),
        "missingStableIdentityCount": missing_identities,
        "stableIdentityDropRatio": round(identity_drop_ratio, 6),
        "departmentDrops": sorted(
            department_drops,
            key=lambda item: (-item["missing"], item["department"]),
        )[:50],
        "baselinePopulatedClassTimeCount": baseline_schedule_count,
        "populatedClassTimeCount": current_schedule_count,
        "classTimeDropRatio": round(schedule_drop_ratio, 6),
        "baselineEnrollmentNonzeroCounts": baseline_nonzero,
        "enrollmentNonzeroCounts": current_nonzero,
        "dynamicDepartmentCollapses": dynamic_department_collapses[:50],
        "sourcePolicy": "strict_fallback" if fallback else "official",
    }
    return ValidationReport(not errors, errors, warnings, stats)


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
