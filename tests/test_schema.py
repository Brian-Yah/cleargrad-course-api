from __future__ import annotations

import json
from pathlib import Path

from jsonschema import Draft202012Validator

ROOT = Path(__file__).parents[1]


def test_course_fixture_satisfies_public_schema() -> None:
    schema = json.loads((ROOT / "schemas" / "course.schema.json").read_text(encoding="utf-8"))
    rows = json.loads((ROOT / "tests" / "fixtures" / "courses.json").read_text(encoding="utf-8"))
    validator = Draft202012Validator(schema)
    assert [error.message for row in rows for error in validator.iter_errors(row)] == []


def test_all_public_schemas_are_valid_draft_2020_12_documents() -> None:
    for path in (ROOT / "schemas").glob("*.json"):
        schema = json.loads(path.read_text(encoding="utf-8"))
        Draft202012Validator.check_schema(schema)
