from __future__ import annotations

import json
import os
import subprocess
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

from jsonschema import Draft202012Validator

from cleargrad_course_api.cadence import (
    cadence_due,
    defer_cadence_after_failure,
    update_cadence_state,
)

NOW = datetime(2026, 6, 25, 0, 0, tzinfo=UTC)
ROOT = Path(__file__).parents[1]


def course(*, select: int = 0, selected: int = 0, remaining: int = 50) -> dict:
    return {
        "department": "測試學系",
        "id": "TEST100",
        "grade": "1",
        "class": "A",
        "restrict": 50,
        "select": select,
        "selected": selected,
        "remaining": remaining,
    }


def test_new_semester_enters_warm_mode_for_course_changes() -> None:
    state = update_cadence_state(
        None,
        semester="1152",
        courses=[course()],
        source_name="NSYSUOfficial",
        now=NOW,
    )

    assert state["mode"] == "warm"
    assert state["currentSemester"] == "1152"
    assert state["nextFullSyncAt"] == "2026-06-25T02:00:00Z"
    assert state["highFrequencyStartedAt"] is None


def test_first_selection_activity_starts_three_weeks_of_high_frequency() -> None:
    warm = update_cadence_state(
        None,
        semester="1152",
        courses=[course()],
        source_name="NSYSUOfficial",
        now=NOW,
    )
    activity_time = NOW + timedelta(days=45)
    high = update_cadence_state(
        warm,
        semester="1152",
        courses=[course(select=1)],
        source_name="NSYSUOfficial",
        now=activity_time,
    )

    assert high["mode"] == "high"
    assert high["highFrequencyStartedAt"] == "2026-08-09T00:00:00Z"
    assert high["highFrequencyUntil"] == "2026-08-30T00:00:00Z"
    assert high["nextFullSyncAt"] == "2026-08-09T00:15:00Z"


def test_high_frequency_does_not_restart_after_the_three_week_window() -> None:
    high = update_cadence_state(
        None,
        semester="1152",
        courses=[course(select=1)],
        source_name="NSYSUOfficial",
        now=NOW,
    )
    after_window = NOW + timedelta(days=21, minutes=1)
    quiet = update_cadence_state(
        high,
        semester="1152",
        courses=[course(select=20, selected=10)],
        source_name="NSYSUOfficial",
        now=after_window,
    )

    assert quiet["mode"] == "quiet"
    assert quiet["highFrequencyStartedAt"] == "2026-06-25T00:00:00Z"
    assert quiet["nextFullSyncAt"] == "2026-07-16T12:01:00Z"


def test_quiet_mode_runs_twice_daily_and_new_semester_restarts_warm_cycle() -> None:
    old = update_cadence_state(
        None,
        semester="1151",
        courses=[course()],
        source_name="NSYSUOfficial",
        now=NOW,
    )
    quiet_time = NOW + timedelta(days=36)
    quiet = update_cadence_state(
        old,
        semester="1151",
        courses=[course()],
        source_name="NSYSUOfficial",
        now=quiet_time,
    )
    restarted = update_cadence_state(
        quiet,
        semester="1152",
        courses=[course()],
        source_name="NSYSUOfficial",
        now=quiet_time + timedelta(hours=12),
    )

    assert quiet["mode"] == "quiet"
    assert quiet["nextFullSyncAt"] == "2026-07-31T12:00:00Z"
    assert restarted["mode"] == "warm"
    assert restarted["semesterDetectedAt"] == "2026-07-31T12:00:00Z"


def test_fallback_retries_official_within_one_hour() -> None:
    state = update_cadence_state(
        None,
        semester="1152",
        courses=[course()],
        source_name="NSYSUCourseAPI",
        now=NOW,
    )

    assert state["mode"] == "warm"
    assert state["nextFullSyncAt"] == "2026-06-25T01:00:00Z"


def test_total_failure_backs_off_except_during_selection_high_mode() -> None:
    quiet = update_cadence_state(
        None,
        semester="1152",
        courses=[course()],
        source_name="NSYSUOfficial",
        now=NOW,
    )
    quiet["mode"] = "quiet"
    quiet_retry = defer_cadence_after_failure(quiet, now=NOW)
    assert quiet_retry is not None
    assert quiet_retry["nextFullSyncAt"] == "2026-06-25T01:00:00Z"

    high = update_cadence_state(
        None,
        semester="1152",
        courses=[course(select=1)],
        source_name="NSYSUOfficial",
        now=NOW,
    )
    high_retry = defer_cadence_after_failure(high, now=NOW)
    assert high_retry is not None
    assert high_retry["nextFullSyncAt"] == "2026-06-25T00:15:00Z"


def test_due_gate_fails_open_and_respects_next_sync_time() -> None:
    assert cadence_due(None, now=NOW) == (True, "state_missing")
    state = update_cadence_state(
        None,
        semester="1152",
        courses=[course()],
        source_name="NSYSUOfficial",
        now=NOW,
    )
    assert cadence_due(state, now=NOW + timedelta(hours=1)) == (False, "not_due")
    assert cadence_due(state, now=NOW + timedelta(hours=2)) == (
        True,
        "interval_elapsed",
    )


def test_lightweight_gate_writes_github_outputs(tmp_path: Path) -> None:
    state = update_cadence_state(
        None,
        semester="1152",
        courses=[course()],
        source_name="NSYSUOfficial",
        now=datetime.now(UTC),
    )
    state_path = tmp_path / "cadence.json"
    state_path.write_text(json.dumps(state), encoding="utf-8")
    output_path = tmp_path / "github-output.txt"
    environment = {**os.environ, "GITHUB_OUTPUT": str(output_path)}

    result = subprocess.run(
        [
            sys.executable,
            str(ROOT / "scripts/check_cadence.py"),
            "--url",
            state_path.as_uri(),
        ],
        check=True,
        capture_output=True,
        text=True,
        env=environment,
    )

    assert json.loads(result.stdout)["due"] is False
    assert "due=false" in output_path.read_text(encoding="utf-8")

    forced_output_path = tmp_path / "forced-github-output.txt"
    forced_environment = {**os.environ, "GITHUB_OUTPUT": str(forced_output_path)}
    forced = subprocess.run(
        [
            sys.executable,
            str(ROOT / "scripts/check_cadence.py"),
            "--url",
            state_path.as_uri(),
            "--force",
        ],
        check=True,
        capture_output=True,
        text=True,
        env=forced_environment,
    )

    assert json.loads(forced.stdout)["reason"] == "manual_dispatch"
    assert "due=true" in forced_output_path.read_text(encoding="utf-8")


def test_generated_cadence_state_satisfies_public_schema() -> None:
    state = update_cadence_state(
        None,
        semester="1152",
        courses=[course(select=1)],
        source_name="NSYSUOfficial",
        now=NOW,
    )
    schema = json.loads(
        (ROOT / "schemas" / "cadence.schema.json").read_text(encoding="utf-8")
    )

    assert [
        error.message for error in Draft202012Validator(schema).iter_errors(state)
    ] == []
