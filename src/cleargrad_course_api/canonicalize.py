from __future__ import annotations

from collections import defaultdict
import json
from typing import Any

from .validation import section_key


def _fingerprint(row: dict[str, Any]) -> str:
    return json.dumps(row, ensure_ascii=False, separators=(",", ":"), sort_keys=True)


def _differing_fields(rows: list[dict[str, Any]]) -> set[str]:
    first = rows[0]
    return {
        field
        for row in rows[1:]
        for field in set(first) | set(row)
        if first.get(field) != row.get(field)
    }


def _information_rank(row: dict[str, Any]) -> tuple[int, int, str]:
    description_length = len(str(row.get("description") or ""))
    populated_fields = sum(value not in (None, "", [], {}) for value in row.values())
    return description_length, populated_fields, _fingerprint(row)


def canonicalize_courses(
    rows: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Remove provably redundant rows without hiding unresolved differences.

    Rows are first grouped by a conservative section identity that includes the
    class label. Exact copies collapse to one. If the only remaining difference
    is description text, the most informative description wins. Any other
    conflict remains in the canonical payload and is reported for review.
    """

    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    group_order: list[str] = []
    for row in rows:
        identity = section_key(row)
        if identity not in grouped:
            group_order.append(identity)
        grouped[identity].append(row)

    canonical: list[dict[str, Any]] = []
    exact_duplicates_removed = 0
    description_variants_merged = 0
    unresolved_conflicts: list[dict[str, Any]] = []

    for identity in group_order:
        group = grouped[identity]
        unique_by_content: dict[str, dict[str, Any]] = {}
        for row in group:
            unique_by_content.setdefault(_fingerprint(row), row)
        unique_rows = list(unique_by_content.values())
        exact_duplicates_removed += len(group) - len(unique_rows)

        if len(unique_rows) == 1:
            canonical.append(unique_rows[0])
            continue

        differing = _differing_fields(unique_rows)
        if differing.issubset({"description"}):
            canonical.append(max(unique_rows, key=_information_rank))
            description_variants_merged += len(unique_rows) - 1
            continue

        canonical.extend(unique_rows)
        unresolved_conflicts.append(
            {
                "sectionKey": identity,
                "courseId": unique_rows[0].get("id"),
                "variantCount": len(unique_rows),
                "differingFields": sorted(differing),
            }
        )

    stats = {
        "rawCourseCount": len(rows),
        "canonicalCourseCount": len(canonical),
        "removedCourseCount": len(rows) - len(canonical),
        "exactDuplicatesRemoved": exact_duplicates_removed,
        "descriptionVariantsMerged": description_variants_merged,
        "unresolvedConflictGroupCount": len(unresolved_conflicts),
        "unresolvedConflicts": unresolved_conflicts[:100],
        "conflictsTruncated": len(unresolved_conflicts) > 100,
    }
    return canonical, stats

