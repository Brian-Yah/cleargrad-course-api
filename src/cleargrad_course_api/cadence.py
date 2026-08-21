from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable
from datetime import UTC, datetime, timedelta
from typing import Any

CADENCE_SCHEMA_VERSION = "1.0.0"
QUIET_INTERVAL = timedelta(hours=12)
WARM_INTERVAL = timedelta(hours=2)
HIGH_INTERVAL = timedelta(minutes=15)
FALLBACK_RETRY_INTERVAL = timedelta(hours=1)
WARM_DURATION = timedelta(days=35)
HIGH_DURATION = timedelta(days=21)


def _utc(value: datetime | None = None) -> datetime:
    current = value or datetime.now(UTC)
    if current.tzinfo is None:
        return current.replace(tzinfo=UTC)
    return current.astimezone(UTC)


def _iso(value: datetime) -> str:
    return _utc(value).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _parse_iso(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    return _utc(parsed)


def _number(value: Any) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def enrollment_activity_present(courses: Iterable[dict[str, Any]]) -> bool:
    """Return true once real selection activity appears.

    Remaining capacity is deliberately excluded: the university may publish a
    capacity before students can click or be selected.
    """

    return any(
        _number(course.get("select")) > 0 or _number(course.get("selected")) > 0
        for course in courses
    )


def enrollment_fingerprint(courses: Iterable[dict[str, Any]]) -> str:
    dynamic_rows = sorted(
        (
            str(course.get("department") or ""),
            str(course.get("id") or ""),
            str(course.get("grade") or ""),
            str(course.get("class") or ""),
            _number(course.get("restrict")),
            _number(course.get("select")),
            _number(course.get("selected")),
            _number(course.get("remaining")),
        )
        for course in courses
    )
    payload = json.dumps(dynamic_rows, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def cadence_due(
    state: dict[str, Any] | None,
    *,
    now: datetime | None = None,
) -> tuple[bool, str]:
    current = _utc(now)
    if not isinstance(state, dict):
        return True, "state_missing"
    if state.get("schemaVersion") != CADENCE_SCHEMA_VERSION:
        return True, "state_schema_changed"
    next_due = _parse_iso(state.get("nextFullSyncAt"))
    if next_due is None:
        return True, "next_sync_missing"
    if current >= next_due:
        return True, "interval_elapsed"
    return False, "not_due"


def defer_cadence_after_failure(
    previous: dict[str, Any] | None,
    *,
    now: datetime | None = None,
) -> dict[str, Any] | None:
    """Move a due state forward without changing its last-known-good data.

    Selection season keeps the 15-minute recovery target. Other modes retry
    hourly so a temporary outage cannot turn a quiet-period check into a
    continuous crawl loop.
    """

    if not isinstance(previous, dict):
        return None
    if previous.get("schemaVersion") != CADENCE_SCHEMA_VERSION:
        return None
    if not previous.get("currentSemester"):
        return None
    deferred = dict(previous)
    interval = HIGH_INTERVAL if previous.get("mode") == "high" else FALLBACK_RETRY_INTERVAL
    deferred["nextFullSyncAt"] = _iso(_utc(now) + interval)
    return deferred


def update_cadence_state(
    previous: dict[str, Any] | None,
    *,
    semester: str,
    courses: list[dict[str, Any]],
    source_name: str,
    now: datetime | None = None,
) -> dict[str, Any]:
    current = _utc(now)
    prior = previous if isinstance(previous, dict) else {}
    same_semester = prior.get("currentSemester") == semester
    detected_at = _parse_iso(prior.get("semesterDetectedAt")) if same_semester else None
    detected_at = detected_at or current

    active = enrollment_activity_present(courses)
    fingerprint = enrollment_fingerprint(courses)
    previous_fingerprint = prior.get("enrollmentFingerprint") if same_semester else None
    dynamic_changed = previous_fingerprint is not None and previous_fingerprint != fingerprint
    last_dynamic_change = (
        _parse_iso(prior.get("lastEnrollmentChangeAt")) if same_semester else None
    )
    if active and (dynamic_changed or last_dynamic_change is None):
        last_dynamic_change = current

    high_started = _parse_iso(prior.get("highFrequencyStartedAt")) if same_semester else None
    high_until = _parse_iso(prior.get("highFrequencyUntil")) if same_semester else None
    if active and high_started is None:
        high_started = current
        high_until = current + HIGH_DURATION

    if high_until is not None and current < high_until:
        mode = "high"
        interval = HIGH_INTERVAL
    elif current < detected_at + WARM_DURATION and high_started is None:
        mode = "warm"
        interval = WARM_INTERVAL
    else:
        mode = "quiet"
        interval = QUIET_INTERVAL

    if source_name != "NSYSUOfficial":
        interval = min(interval, FALLBACK_RETRY_INTERVAL)

    return {
        "schemaVersion": CADENCE_SCHEMA_VERSION,
        "currentSemester": semester,
        "mode": mode,
        "semesterDetectedAt": _iso(detected_at),
        "highFrequencyStartedAt": _iso(high_started) if high_started else None,
        "highFrequencyUntil": _iso(high_until) if high_until else None,
        "lastEnrollmentChangeAt": (
            _iso(last_dynamic_change) if last_dynamic_change else None
        ),
        "enrollmentActivityPresent": active,
        "enrollmentFingerprint": fingerprint,
        "lastSuccessfulSyncAt": _iso(current),
        "lastSource": source_name,
        "nextFullSyncAt": _iso(current + interval),
        "policy": {
            "quietIntervalMinutes": int(QUIET_INTERVAL.total_seconds() // 60),
            "warmIntervalMinutes": int(WARM_INTERVAL.total_seconds() // 60),
            "highIntervalMinutes": int(HIGH_INTERVAL.total_seconds() // 60),
            "fallbackRetryIntervalMinutes": int(
                FALLBACK_RETRY_INTERVAL.total_seconds() // 60
            ),
            "warmDurationDays": WARM_DURATION.days,
            "highDurationDays": HIGH_DURATION.days,
            "timezone": "Asia/Taipei",
        },
    }
