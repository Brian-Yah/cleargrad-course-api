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
DEFAULT_MAX_DROP_RATIO = 0.10
DEFAULT_MAX_DUPLICATE_RATIO = 0.05
MAX_VERSION_HISTORY = 5

