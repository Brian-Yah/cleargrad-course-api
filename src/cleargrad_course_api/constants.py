from __future__ import annotations

SCHEMA_VERSION = "1.0.0"
COMPATIBILITY_PROFILE = "NSYSUCourseAPI-v1"

DEFAULT_SOURCE_BASES = (
    "https://nsysu-opendev.github.io/NSYSUCourseAPI",
    "https://raw.githubusercontent.com/NSYSU-OpenDev/NSYSUCourseAPI/gh-pages",
)

REQUIRED_FIELDS = frozenset(
    {
        "url",
        "change",
        "changeDescription",
        "multipleCompulsory",
        "department",
        "id",
        "grade",
        "class",
        "name",
        "credit",
        "yearSemester",
        "compulsory",
        "restrict",
        "select",
        "selected",
        "remaining",
        "teacher",
        "room",
        "classTime",
        "description",
        "tags",
        "english",
    }
)

VOLATILE_FIELDS = frozenset({"select", "selected", "remaining"})
DEFAULT_MINIMUM_COURSE_COUNT = 500
SHORT_SEMESTER_MINIMUM_COURSE_COUNT = 10
DEFAULT_MAX_DROP_RATIO = 0.10
DEFAULT_MAX_DUPLICATE_RATIO = 0.05
MAX_VERSION_HISTORY = 5


def minimum_course_count_for_semester(semester: str) -> int:
    # NSYSU's third semester is a deliberately small summer catalog. Current
    # upstream examples contain about two API pages, so the regular 500-row
    # safety floor would incorrectly reject valid 1123/1133/1143 snapshots.
    if semester.endswith("3"):
        return SHORT_SEMESTER_MINIMUM_COURSE_COUNT
    return DEFAULT_MINIMUM_COURSE_COUNT
