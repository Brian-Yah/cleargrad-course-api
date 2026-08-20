from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

from .constants import DEFAULT_SOURCE_BASES
from .direct import DirectCrawlError, OFFICIAL_BASE_URL, OFFICIAL_RESULTS_URL, crawl_official_courses
from .publisher import (
    FetchedSnapshot,
    PublicationRejected,
    audit_site,
    copy_schemas,
    fetch_upstream_snapshot,
    hydrate_site,
    publish_snapshot,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="cleargrad-course-api")
    subparsers = parser.add_subparsers(dest="command", required=True)

    sync = subparsers.add_parser("sync", help="Fetch, validate, and publish a static snapshot")
    sync.add_argument("--output", type=Path, default=Path("site"))
    sync.add_argument("--reports", type=Path, default=Path("reports"))
    sync.add_argument("--schema-dir", type=Path, default=Path("schemas"))
    sync.add_argument("--semester")
    sync.add_argument("--source-base", action="append", dest="source_bases")
    sync.add_argument(
        "--mirror-only",
        action="store_true",
        help="Skip the official collector and use NSYSUCourseAPI",
    )
    sync.add_argument("--hydrate-from", default="")
    sync.add_argument("--minimum-course-count", type=int)
    sync.add_argument(
        "--no-backfill-missing",
        action="store_true",
        help="Do not automatically publish the latest snapshot of newly discovered semesters",
    )
    sync.add_argument("--max-drop-ratio", type=float, default=0.10)
    sync.add_argument("--timeout", type=float, default=45.0)
    sync.add_argument(
        "--official-timeout",
        type=float,
        default=120.0,
        help="Per-request timeout for the slower official NSYSU course site",
    )
    sync.add_argument(
        "--official-budget-seconds",
        type=float,
        default=600.0,
        help="Maximum total official crawl time before the static fallback is used",
    )

    audit = subparsers.add_parser("audit", help="Validate the latest published snapshots")
    audit.add_argument("--output", type=Path, default=Path("site"))
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.command == "audit":
        result = audit_site(args.output)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0 if result["ok"] else 1

    args.output.mkdir(parents=True, exist_ok=True)
    notices = hydrate_site(args.hydrate_from, args.output) if args.hydrate_from else []
    for notice in notices:
        print(f"warning: {notice}", file=sys.stderr)
    source_bases = tuple(args.source_bases or DEFAULT_SOURCE_BASES)
    try:
        direct_failure: str | None = None
        if args.mirror_only:
            snapshot = fetch_upstream_snapshot(
                source_bases,
                semester=args.semester,
                timeout=args.timeout,
            )
        else:
            try:
                official = crawl_official_courses(
                    semester=args.semester,
                    timeout=args.official_timeout,
                    max_duration=args.official_budget_seconds,
                )
                source_version = official.retrieved_at.strftime("%Y%m%d_%H%M%S")
                source_time = official.retrieved_at.replace(microsecond=0).isoformat().replace(
                    "+00:00", "Z"
                )
                snapshot = FetchedSnapshot(
                    semester=official.semester,
                    source_version=source_version,
                    source_version_time=source_time,
                    courses=official.courses,
                    source_base=OFFICIAL_BASE_URL,
                    source_root={
                        "latest": official.semester,
                        "history": official.semester_history,
                    },
                    source_name="NSYSUOfficial",
                    source_url=OFFICIAL_RESULTS_URL,
                )
            except DirectCrawlError as error:
                direct_failure = str(error)
                print(
                    f"warning: official NSYSU crawl failed; trying NSYSUCourseAPI fallback: {error}",
                    file=sys.stderr,
                )
                snapshot = fetch_upstream_snapshot(
                    source_bases,
                    semester=args.semester,
                    timeout=args.timeout,
                )
        result = publish_snapshot(
            snapshot,
            args.output,
            report_dir=args.reports,
            minimum_course_count=args.minimum_course_count,
            max_drop_ratio=args.max_drop_ratio,
        )
        backfilled = []
        if not args.no_backfill_missing:
            discovered_semesters = sorted(snapshot.source_root.get("history", {}))
            for discovered_semester in discovered_semesters:
                if discovered_semester == snapshot.semester:
                    continue
                if (args.output / discovered_semester / "version.json").is_file():
                    continue
                historical = fetch_upstream_snapshot(
                    source_bases,
                    semester=discovered_semester,
                    timeout=args.timeout,
                )
                historical_result = publish_snapshot(
                    historical,
                    args.output,
                    report_dir=args.reports,
                    minimum_course_count=args.minimum_course_count,
                    max_drop_ratio=args.max_drop_ratio,
                )
                backfilled.append(
                    {
                        "semester": historical_result.semester,
                        "version": historical_result.version,
                        "courseCount": historical_result.course_count,
                    }
                )
        copy_schemas(args.schema_dir, args.output)
    except PublicationRejected as error:
        print(f"publication rejected; last-known-good remains active: {error}", file=sys.stderr)
        return 2
    except Exception as error:
        print(f"sync failed; last-known-good remains active: {error}", file=sys.stderr)
        return 1

    print(
        json.dumps(
            {
                "changed": result.changed,
                "semester": result.semester,
                "version": result.version,
                "courseCount": result.course_count,
                "sourceBase": result.source_base,
                "source": result.source_name,
                "ignoredReason": result.ignored_reason,
                "directCrawlFailure": direct_failure,
                "manifest": str(result.manifest_path),
                "backfilled": backfilled,
            },
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
