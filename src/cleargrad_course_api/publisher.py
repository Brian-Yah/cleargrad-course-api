from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import json
import math
from pathlib import Path
import re
import shutil
from typing import Any, Iterable

from .constants import (
    COMPATIBILITY_PROFILE,
    DEFAULT_MAX_DROP_RATIO,
    MAX_VERSION_HISTORY,
    SCHEMA_VERSION,
    minimum_course_count_for_semester,
)
from .canonicalize import canonicalize_courses
from .diffing import diff_courses
from .io import FetchError, fetch_bytes, fetch_json, join_url, read_json, sha256_json, write_json
from .validation import ValidationReport, validate_continuity, validate_courses

SEMESTER_PATTERN = re.compile(r"^[0-9]{4}$")
VERSION_PATTERN = re.compile(r"^[0-9]{8}_[0-9]{6}$")


class PublicationRejected(RuntimeError):
    """Raised when a fetched snapshot does not pass safety gates."""


@dataclass(frozen=True)
class FetchedSnapshot:
    semester: str
    source_version: str
    source_version_time: str
    courses: list[dict[str, Any]]
    source_base: str
    source_root: dict[str, Any]
    source_name: str = "NSYSUCourseAPI"
    source_url: str | None = None


@dataclass(frozen=True)
class PublishResult:
    changed: bool
    semester: str
    version: str
    course_count: int
    source_base: str
    manifest_path: Path
    source_name: str
    ignored_reason: str | None = None


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def iso_utc(value: datetime | None = None) -> str:
    return (value or utc_now()).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _validate_version_document(value: Any, label: str) -> dict[str, Any]:
    if not isinstance(value, dict) or not isinstance(value.get("latest"), str):
        raise FetchError(f"{label} does not contain a string latest version")
    if not isinstance(value.get("history"), dict):
        raise FetchError(f"{label} does not contain a history object")
    return value


def fetch_upstream_snapshot(
    source_bases: Iterable[str],
    *,
    semester: str | None = None,
    timeout: float = 45.0,
) -> FetchedSnapshot:
    failures: list[str] = []
    for source_base in source_bases:
        try:
            root = _validate_version_document(
                fetch_json(join_url(source_base, "version.json"), timeout=timeout),
                "root version.json",
            )
            target_semester = semester or root["latest"]
            if not SEMESTER_PATTERN.fullmatch(target_semester):
                raise FetchError(f"invalid semester identifier: {target_semester!r}")
            semester_version = _validate_version_document(
                fetch_json(
                    join_url(source_base, target_semester, "version.json"),
                    timeout=timeout,
                ),
                f"{target_semester}/version.json",
            )
            source_version = semester_version["latest"]
            if not VERSION_PATTERN.fullmatch(source_version):
                raise FetchError(f"invalid source version identifier: {source_version!r}")
            courses = fetch_json(
                join_url(source_base, target_semester, source_version, "all.json"),
                timeout=timeout,
            )
            if not isinstance(courses, list):
                raise FetchError("all.json root is not an array")
            source_time = str(semester_version["history"].get(source_version) or "")
            return FetchedSnapshot(
                semester=target_semester,
                source_version=source_version,
                source_version_time=source_time,
                courses=courses,
                source_base=source_base.rstrip("/"),
                source_root=root,
                source_name="NSYSUCourseAPI",
                source_url=join_url(
                    source_base,
                    target_semester,
                    source_version,
                    "all.json",
                ),
            )
        except (FetchError, KeyError, TypeError, ValueError) as error:
            failures.append(f"{source_base}: {error}")
    raise FetchError("all upstream sources failed: " + " | ".join(failures))


def _trim_history(history: dict[str, str]) -> dict[str, str]:
    ordered = sorted(history.items(), key=lambda item: item[0])[-MAX_VERSION_HISTORY:]
    return dict(ordered)


def _load_previous(output: Path, semester: str) -> tuple[str | None, list[dict[str, Any]]]:
    version_document = read_json(output / semester / "version.json", {})
    previous_version = version_document.get("latest") if isinstance(version_document, dict) else None
    if not isinstance(previous_version, str) or not VERSION_PATTERN.fullmatch(previous_version):
        return None, []
    previous = read_json(output / semester / previous_version / "all.json", [])
    return previous_version, previous if isinstance(previous, list) else []


def _load_latest_official_baseline(output: Path, semester: str) -> list[dict[str, Any]]:
    """Load the newest retained direct-official snapshot for fallback comparison."""
    retained = read_json(output / "official-baseline" / semester / "all.json", [])
    if isinstance(retained, list) and retained:
        return retained

    # Compatibility path for deployments created before the durable baseline
    # was introduced. A future direct-official publication writes the retained
    # copy below, so it cannot fall out of the five-snapshot hot history.
    version_document = read_json(output / semester / "version.json", {})
    history = version_document.get("history", {}) if isinstance(version_document, dict) else {}
    if not isinstance(history, dict):
        return []
    for version in sorted(history, reverse=True):
        if not isinstance(version, str) or not VERSION_PATTERN.fullmatch(version):
            continue
        snapshot_dir = output / semester / version
        manifest = read_json(snapshot_dir / "manifest.json", {})
        if not isinstance(manifest, dict) or manifest.get("source") != "NSYSUOfficial":
            continue
        courses = read_json(snapshot_dir / "all.json", [])
        if isinstance(courses, list):
            return courses
    return []


def _write_failure_report(
    report_dir: Path | None,
    snapshot: FetchedSnapshot,
    report: ValidationReport,
    now: datetime,
) -> None:
    if report_dir is None:
        return
    write_json(
        report_dir / "last-rejected.json",
        {
            "schemaVersion": SCHEMA_VERSION,
            "rejectedAt": iso_utc(now),
            "semester": snapshot.semester,
            "source": snapshot.source_name,
            "sourceVersion": snapshot.source_version,
            "sourceBase": snapshot.source_base,
            "validation": report.to_dict(),
        },
        pretty=True,
    )


def publish_snapshot(
    snapshot: FetchedSnapshot,
    output: Path,
    *,
    report_dir: Path | None = None,
    minimum_course_count: int | None = None,
    max_drop_ratio: float = DEFAULT_MAX_DROP_RATIO,
    now: datetime | None = None,
) -> PublishResult:
    current_time = now or utc_now()
    effective_minimum = (
        minimum_course_count
        if minimum_course_count is not None
        else minimum_course_count_for_semester(snapshot.semester)
    )
    previous_version, previous_courses = _load_previous(output, snapshot.semester)
    canonical_courses, canonicalization = canonicalize_courses(snapshot.courses)
    checksum = sha256_json(canonical_courses)
    raw_checksum = sha256_json(snapshot.courses)

    if previous_version is not None:
        previous_manifest_path = output / snapshot.semester / previous_version / "manifest.json"
        previous_manifest = read_json(previous_manifest_path, {})
        previous_source = str(previous_manifest.get("source") or "unknown")
        previous_source_base = str(previous_manifest.get("sourceBase") or snapshot.source_base)
        if (
            previous_manifest.get("sha256") == checksum
            and previous_manifest.get("rawSha256") == raw_checksum
        ):
            return PublishResult(
                changed=False,
                semester=snapshot.semester,
                version=previous_version,
                course_count=len(canonical_courses),
                source_base=previous_source_base,
                manifest_path=previous_manifest_path,
                source_name=previous_source,
                ignored_reason="content_unchanged",
            )
        if snapshot.source_version < previous_version:
            return PublishResult(
                changed=False,
                semester=snapshot.semester,
                version=previous_version,
                course_count=len(previous_courses),
                source_base=previous_source_base,
                manifest_path=previous_manifest_path,
                source_name=previous_source,
                ignored_reason="out_of_order_candidate",
            )

    current_snapshot_path = output / snapshot.semester / snapshot.source_version / "all.json"
    if previous_version == snapshot.source_version and current_snapshot_path.is_file():
        manifest_path = current_snapshot_path.parent / "manifest.json"
        existing_manifest = read_json(manifest_path, {})
        if existing_manifest.get("rawSha256") != raw_checksum:
            report = ValidationReport(
                ok=False,
                errors=[
                    "upstream content changed without a new source version; immutable snapshot rejected"
                ],
                stats={"courseCount": len(snapshot.courses)},
            )
            _write_failure_report(report_dir, snapshot, report, current_time)
            raise PublicationRejected(report.errors[0])
        return PublishResult(
            changed=False,
            semester=snapshot.semester,
            version=snapshot.source_version,
            course_count=len(canonical_courses),
            source_base=snapshot.source_base,
            manifest_path=manifest_path,
            source_name=snapshot.source_name,
            ignored_reason="source_version_unchanged",
        )

    raw_report = validate_courses(
        snapshot.courses,
        previous_courses=previous_courses or None,
        minimum_course_count=effective_minimum,
        max_drop_ratio=max_drop_ratio,
        # Redundancy is evaluated after conservative canonicalization. A high
        # duplicate ratio alone must not reject a snapshot whose canonical
        # payload remains complete and valid.
        max_duplicate_ratio=1.0,
    )
    canonical_report = validate_courses(
        canonical_courses,
        previous_courses=previous_courses or None,
        minimum_course_count=effective_minimum,
        max_drop_ratio=max_drop_ratio,
    )
    continuity_baseline = (
        _load_latest_official_baseline(output, snapshot.semester)
        if snapshot.source_name == "NSYSUCourseAPI"
        else previous_courses
    )
    continuity_report = validate_continuity(
        canonical_courses,
        baseline_courses=continuity_baseline,
        source_name=snapshot.source_name,
    )
    combined_errors = [
        *raw_report.errors,
        *canonical_report.errors,
        *continuity_report.errors,
    ]
    if combined_errors:
        rejected_report = ValidationReport(
            ok=False,
            errors=combined_errors,
            warnings=[
                *raw_report.warnings,
                *canonical_report.warnings,
                *continuity_report.warnings,
            ],
            stats={
                "raw": raw_report.stats,
                "canonical": canonical_report.stats,
                "canonicalization": canonicalization,
                "continuity": continuity_report.stats,
            },
        )
        _write_failure_report(report_dir, snapshot, rejected_report, current_time)
        raise PublicationRejected("; ".join(combined_errors))

    snapshot_dir = output / snapshot.semester / snapshot.source_version
    source_all_url = snapshot.source_url or join_url(
        snapshot.source_base, snapshot.semester, snapshot.source_version, "all.json"
    )
    course_diff = diff_courses(previous_courses, canonical_courses)
    publication_warnings = [*continuity_report.warnings]
    publication_warnings.extend([
        f"{canonicalization['removedCourseCount']} redundant source rows were canonicalized; see all.raw.json"
    ] if canonicalization["removedCourseCount"] else [])
    if canonicalization["unresolvedConflictGroupCount"]:
        publication_warnings.append(
            f"{canonicalization['unresolvedConflictGroupCount']} canonicalization conflicts remain unresolved"
        )
    manifest = {
        "schemaVersion": SCHEMA_VERSION,
        "compatibilityProfile": COMPATIBILITY_PROFILE,
        "semester": snapshot.semester,
        "snapshotId": snapshot.source_version,
        "source": snapshot.source_name,
        "sourceBase": snapshot.source_base,
        "sourceUrl": source_all_url,
        "sourceVersion": snapshot.source_version,
        "sourceVersionTime": snapshot.source_version_time or None,
        "retrievedAt": iso_utc(current_time),
        "publishedAt": iso_utc(current_time),
        "courseCount": len(canonical_courses),
        "rawCourseCount": len(snapshot.courses),
        "sha256": checksum,
        "rawSha256": raw_checksum,
        "validationStatus": "passed",
        "warnings": publication_warnings,
        "canonicalization": canonicalization,
        "previousSnapshotId": previous_version,
    }
    validation_payload = {
        "schemaVersion": SCHEMA_VERSION,
        "validatedAt": iso_utc(current_time),
        "semester": snapshot.semester,
        "snapshotId": snapshot.source_version,
        "ok": True,
        "errors": [],
        "warnings": publication_warnings,
        "stats": {
            "raw": raw_report.stats,
            "canonical": canonical_report.stats,
            "canonicalization": canonicalization,
            "continuity": continuity_report.stats,
        },
    }
    diff_payload = {
        "schemaVersion": SCHEMA_VERSION,
        "semester": snapshot.semester,
        "fromSnapshotId": previous_version,
        "toSnapshotId": snapshot.source_version,
        "generatedAt": iso_utc(current_time),
        **course_diff,
    }
    info = {
        "page_size": math.ceil(len(canonical_courses) / 20),
        "updated": snapshot.source_version,
        "course_count": len(canonical_courses),
        "raw_course_count": len(snapshot.courses),
        "sha256": checksum,
        "validation_status": "passed",
    }

    write_json(snapshot_dir / "all.json", canonical_courses)
    write_json(snapshot_dir / "all.raw.json", snapshot.courses)
    write_json(snapshot_dir / "info.json", info)
    write_json(snapshot_dir / "manifest.json", manifest, pretty=True)
    write_json(snapshot_dir / "validation.json", validation_payload, pretty=True)
    write_json(snapshot_dir / "diff.json", diff_payload, pretty=True)
    (snapshot_dir / "diff.txt").write_text(
        "\n".join(
            [
                f"from: {previous_version or 'none'}",
                f"to: {snapshot.source_version}",
                f"courses: {len(previous_courses)} -> {len(canonical_courses)} (raw {len(snapshot.courses)})",
                f"added sections: {course_diff['addedSectionCount']}",
                f"removed sections: {course_diff['removedSectionCount']}",
                f"changed sections: {course_diff['changedSectionCount']}",
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    semester_version_path = output / snapshot.semester / "version.json"
    semester_document = read_json(semester_version_path, {})
    semester_history = (
        dict(semester_document.get("history", {}))
        if isinstance(semester_document, dict)
        else {}
    )
    semester_history[snapshot.source_version] = snapshot.source_version_time or iso_utc(current_time)
    write_json(
        semester_version_path,
        {"latest": snapshot.source_version, "history": _trim_history(semester_history)},
        pretty=True,
    )

    root_path = output / "version.json"
    root_document = read_json(root_path, {})
    root_history = dict(root_document.get("history", {})) if isinstance(root_document, dict) else {}
    semester_label = snapshot.source_root.get("history", {}).get(snapshot.semester)
    root_history[snapshot.semester] = semester_label or snapshot.semester
    latest_semester = max(root_history) if root_history else snapshot.semester
    write_json(root_path, {"latest": latest_semester, "history": dict(sorted(root_history.items()))}, pretty=True)

    lkg_dir = output / "lkg" / snapshot.semester
    write_json(lkg_dir / "all.json", canonical_courses)
    write_json(lkg_dir / "all.raw.json", snapshot.courses)
    write_json(lkg_dir / "manifest.json", manifest, pretty=True)
    if snapshot.source_name == "NSYSUOfficial":
        official_baseline_dir = output / "official-baseline" / snapshot.semester
        write_json(official_baseline_dir / "all.json", canonical_courses)
        write_json(official_baseline_dir / "manifest.json", manifest, pretty=True)
    write_json(output / "health.json", {
        "schemaVersion": SCHEMA_VERSION,
        "status": "ok",
        "lastSuccessfulSync": iso_utc(current_time),
        "semester": snapshot.semester,
        "snapshotId": snapshot.source_version,
        "courseCount": len(canonical_courses),
        "rawCourseCount": len(snapshot.courses),
        "sha256": checksum,
    }, pretty=True)

    return PublishResult(
        changed=True,
        semester=snapshot.semester,
        version=snapshot.source_version,
        course_count=len(canonical_courses),
        source_base=snapshot.source_base,
        manifest_path=snapshot_dir / "manifest.json",
        source_name=snapshot.source_name,
    )


def copy_schemas(schema_dir: Path, output: Path) -> None:
    (output / ".nojekyll").touch()
    if not schema_dir.is_dir():
        return
    target = output / "schemas"
    target.mkdir(parents=True, exist_ok=True)
    for schema in schema_dir.glob("*.json"):
        shutil.copy2(schema, target / schema.name)


def hydrate_site(public_base_url: str, output: Path) -> list[str]:
    """Restore the prior Pages payload before creating the next immutable snapshot."""
    notices: list[str] = []
    if not public_base_url:
        return notices
    base = public_base_url.rstrip("/")
    try:
        root = _validate_version_document(fetch_json(join_url(base, "version.json"), attempts=1), "published root")
    except FetchError as error:
        notices.append(f"hydrate skipped: {error}")
        return notices

    write_json(output / "version.json", root, pretty=True)
    for singleton in ("health.json",):
        try:
            (output / singleton).write_bytes(fetch_bytes(join_url(base, singleton), attempts=1))
        except FetchError:
            pass

    for semester in root.get("history", {}):
        if not SEMESTER_PATTERN.fullmatch(str(semester)):
            continue
        try:
            semester_document = _validate_version_document(
                fetch_json(join_url(base, semester, "version.json"), attempts=1),
                f"published {semester}",
            )
        except FetchError as error:
            notices.append(str(error))
            continue
        write_json(output / semester / "version.json", semester_document, pretty=True)
        versions = list(semester_document.get("history", {}))[-MAX_VERSION_HISTORY:]
        for version in versions:
            if not VERSION_PATTERN.fullmatch(str(version)):
                continue
            for filename in ("all.json", "all.raw.json", "info.json", "manifest.json", "validation.json", "diff.json", "diff.txt"):
                try:
                    target = output / semester / version / filename
                    target.parent.mkdir(parents=True, exist_ok=True)
                    target.write_bytes(
                        fetch_bytes(join_url(base, semester, version, filename), attempts=1)
                    )
                except FetchError:
                    if filename == "all.json":
                        notices.append(f"published snapshot missing: {semester}/{version}/all.json")
        latest = semester_document.get("latest")
        if isinstance(latest, str):
            for filename in ("all.json", "all.raw.json", "manifest.json"):
                source = output / semester / latest / filename
                if source.is_file():
                    target = output / "lkg" / semester / filename
                    target.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(source, target)
        for filename in ("all.json", "manifest.json"):
            try:
                target = output / "official-baseline" / semester / filename
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(
                    fetch_bytes(
                        join_url(base, "official-baseline", semester, filename),
                        attempts=1,
                    )
                )
            except FetchError:
                pass
    return notices


def audit_site(output: Path) -> dict[str, Any]:
    root = _validate_version_document(read_json(output / "version.json"), "local root")
    failures: list[str] = []
    checked: list[dict[str, Any]] = []
    for semester in root["history"]:
        semester_document = _validate_version_document(
            read_json(output / semester / "version.json"), f"local {semester}"
        )
        latest = semester_document["latest"]
        courses = read_json(output / semester / latest / "all.json")
        manifest = read_json(output / semester / latest / "manifest.json", {})
        report = validate_courses(
            courses,
            minimum_course_count=minimum_course_count_for_semester(semester),
        )
        checksum = sha256_json(courses) if isinstance(courses, list) else None
        if not report.ok:
            failures.extend(f"{semester}: {error}" for error in report.errors)
        if manifest.get("sha256") != checksum:
            failures.append(f"{semester}: manifest checksum mismatch")
        checked.append({"semester": semester, "snapshotId": latest, "courseCount": len(courses or [])})
    return {"ok": not failures, "failures": failures, "checked": checked}
