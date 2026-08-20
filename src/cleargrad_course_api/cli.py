from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

from .constants import DEFAULT_SOURCE_BASES
from .publisher import (
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
    sync.add_argument("--hydrate-from", default="")
    sync.add_argument("--minimum-course-count", type=int, default=500)
    sync.add_argument("--max-drop-ratio", type=float, default=0.10)
    sync.add_argument("--timeout", type=float, default=45.0)

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
                "manifest": str(result.manifest_path),
            },
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

