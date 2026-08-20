from __future__ import annotations

from collections import Counter
from typing import Any

from .constants import VOLATILE_FIELDS
from .validation import section_key


def _index(rows: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    return {section_key(row): row for row in rows}


def diff_courses(
    old_rows: list[dict[str, Any]], new_rows: list[dict[str, Any]]
) -> dict[str, Any]:
    old = _index(old_rows)
    new = _index(new_rows)
    old_keys = set(old)
    new_keys = set(new)
    added = sorted(new_keys - old_keys)
    removed = sorted(old_keys - new_keys)
    shared = old_keys & new_keys
    changed_fields: Counter[str] = Counter()
    changed_sections = 0
    volatile_only_sections = 0

    for key in shared:
        before = old[key]
        after = new[key]
        fields = {
            field
            for field in set(before) | set(after)
            if before.get(field) != after.get(field)
        }
        if not fields:
            continue
        changed_sections += 1
        if fields.issubset(VOLATILE_FIELDS):
            volatile_only_sections += 1
        changed_fields.update(fields)

    return {
        "oldCourseCount": len(old_rows),
        "newCourseCount": len(new_rows),
        "addedSectionCount": len(added),
        "removedSectionCount": len(removed),
        "changedSectionCount": changed_sections,
        "volatileOnlyChangedSectionCount": volatile_only_sections,
        "changedFieldCounts": dict(sorted(changed_fields.items())),
        "addedSectionKeys": added[:100],
        "removedSectionKeys": removed[:100],
        "truncated": len(added) > 100 or len(removed) > 100,
    }

