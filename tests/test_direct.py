from __future__ import annotations

from bs4 import BeautifulSoup
import hashlib
from pathlib import Path

from cleargrad_course_api.direct import (
    MAX_CONCURRENT_REQUESTS,
    MAX_REQUEST_ATTEMPTS,
    parse_course_row,
    parse_semester_index,
)


ROOT = Path(__file__).parents[1]


def test_official_collector_is_patient_but_keeps_low_concurrency() -> None:
    assert MAX_REQUEST_ATTEMPTS == 5
    assert MAX_CONCURRENT_REQUESTS == 2


def test_packaged_captcha_model_is_the_reviewed_onnx_conversion() -> None:
    model = ROOT / "src" / "cleargrad_course_api" / "assets" / "nsysu_captcha.onnx"
    assert hashlib.sha256(model.read_bytes()).hexdigest() == (
        "83283cfa4ea4ca95f33c0895bc71c9d82773d28a119d2d9eea4d74e68ab3f87c"
    )


def test_parse_semester_index_preserves_official_order_and_labels() -> None:
    html = """
    <select id="YRSM">
      <option value="">請選擇</option>
      <option value="1151">115暑碩</option>
      <option value="1143">114下</option>
      <option value="1142">114上</option>
    </select>
    """
    latest, history = parse_semester_index(html)
    assert latest == "1151"
    assert history == {"1151": "115暑碩", "1143": "114下", "1142": "114上"}


def test_parse_official_course_row_keeps_dynamic_enrollment_fields() -> None:
    values = [
        "異動",
        "8/20",
        "*",
        "資訊工程學系",
        "CSE101",
        "1",
        "A",
        '<small><a href="/menu5/showoutline.asp?CrsDat=CSE101">程式設計<br>PROGRAMMING</a></small>',
        "3",
        "期",
        "必",
        "60",
        "65",
        "52",
        "8",
        "測試教師",
        "一1,2,3(工EC 1001)",
        "123",
        "",
        "",
        "",
        "",
        "",
        "",
        "《講授類》※英語授課<font>國際學程</font>",
    ]
    cells = "".join(f"<td>{value}</td>" for value in values)
    row = BeautifulSoup(f'<tr bgcolor="#fff">{cells}</tr>', "html.parser").select_one("tr")
    assert row is not None
    course = parse_course_row(row)
    assert course["id"] == "CSE101"
    assert course["select"] == 65
    assert course["selected"] == 52
    assert course["remaining"] == 8
    assert course["restrict"] == 60
    assert course["english"] is True
    assert course["tags"] == ["國際學程"]
    assert course["name"] == "程式設計\nPROGRAMMING"
