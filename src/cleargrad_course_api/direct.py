from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import datetime, timezone
import io
from importlib.resources import files
import re
import ssl
import time
from typing import Any, Callable
from urllib.parse import urljoin

import aiohttp
from bs4 import BeautifulSoup, Tag
import numpy as np
import onnxruntime as ort
from PIL import Image, ImageFilter


OFFICIAL_BASE_URL = "https://selcrs.nsysu.edu.tw/menu1"
OFFICIAL_QUERY_URL = f"{OFFICIAL_BASE_URL}/qrycourse.asp?HIS=2"
OFFICIAL_RESULTS_URL = f"{OFFICIAL_BASE_URL}/dplycourse.asp"
OFFICIAL_CAPTCHA_URL = f"{OFFICIAL_BASE_URL}/validcode.asp"
DEFAULT_HEADERS = {
    "User-Agent": (
        "ClearGrad-Course-API/0.2 "
        "(+https://github.com/Brian-Yah/cleargrad-course-api)"
    ),
}
MAX_CONCURRENT_REQUESTS = 2
MAX_REQUEST_ATTEMPTS = 5
MAX_CAPTCHA_ATTEMPTS = 8
PAGE_COUNT_PATTERN = re.compile(r"Showing page\s+\d+\s+of\s+(\d+)\s+pages", re.I)


class DirectCrawlError(RuntimeError):
    """Raised when the official NSYSU course site cannot be crawled safely."""


@dataclass(frozen=True)
class OfficialCrawlResult:
    semester: str
    semester_history: dict[str, str]
    courses: list[dict[str, Any]]
    page_count: int
    retrieved_at: datetime


def semester_label(semester: str) -> str:
    labels = {"1": "暑碩", "2": "上", "3": "下", "4": "暑期"}
    return f"{semester[:3]}{labels.get(semester[-1], semester[-1])}"


def parse_semester_index(html: str) -> tuple[str, dict[str, str]]:
    soup = BeautifulSoup(html, "html.parser")
    history: dict[str, str] = {}
    for option in soup.select("#YRSM option[value]"):
        semester = str(option.get("value") or "").strip()
        if not re.fullmatch(r"[0-9]{4}", semester):
            continue
        label = option.get_text(" ", strip=True) or semester_label(semester)
        history.setdefault(semester, label)
    if not history:
        raise DirectCrawlError("official query page did not expose any semester options")
    # The official select lists its current/default semester first. Do not use
    # max(): suffixes 1/2/3/4 denote term types, not chronological ordering.
    return next(iter(history)), history


class CaptchaSolver:
    def __init__(self, model_path: str | None = None) -> None:
        path = model_path or str(files("cleargrad_course_api.assets").joinpath("nsysu_captcha.onnx"))
        options = ort.SessionOptions()
        options.intra_op_num_threads = 1
        options.inter_op_num_threads = 1
        self._session = ort.InferenceSession(
            path,
            sess_options=options,
            providers=["CPUExecutionProvider"],
        )

    @staticmethod
    def _prepare(image_bytes: bytes) -> np.ndarray:
        try:
            image = Image.open(io.BytesIO(image_bytes)).convert("L")
        except Exception as error:
            raise DirectCrawlError(f"invalid official captcha image: {error}") from error
        image = image.filter(ImageFilter.MedianFilter(size=3))
        width, height = image.size
        if width < 4 or height < 1:
            raise DirectCrawlError(f"unexpected captcha dimensions: {image.size}")
        slice_width = width // 4
        slices = [
            np.asarray(
                image.crop((index * slice_width, 0, (index + 1) * slice_width, height)).resize(
                    (28, 28)
                ),
                dtype=np.float32,
            )
            / 255.0
            for index in range(4)
        ]
        return np.asarray(slices, dtype=np.float32)[:, np.newaxis, :, :]

    def solve(self, image_bytes: bytes) -> str:
        probabilities = self._session.run(
            ["probabilities"],
            {"digits": self._prepare(image_bytes)},
        )[0]
        classes = np.argmax(probabilities, axis=1) + 1
        if len(classes) != 4 or any(int(value) not in range(1, 10) for value in classes):
            raise DirectCrawlError(f"captcha model returned invalid classes: {classes.tolist()}")
        return "".join(str(int(value)) for value in classes)


def _optional(value: str) -> str | None:
    return value or None


def _integer(value: str, field: str) -> int:
    try:
        return int(value)
    except ValueError as error:
        raise ValueError(f"{field} is not an integer: {value!r}") from error


def parse_course_row(row: Tag) -> dict[str, Any]:
    for line_break in row.find_all("br"):
        line_break.replace_with("\n")
    cells = row.find_all("td", recursive=False)
    # Preserve the newline inserted for <br>; ClearGrad's existing contract
    # stores canonical Chinese and English course names separated by "\n".
    values = [cell.get_text().strip() for cell in cells]
    if len(values) < 25:
        raise ValueError(f"expected at least 25 cells, received {len(values)}")

    (
        change,
        change_description,
        multiple_compulsory,
        department,
        course_id,
        grade,
        course_class,
        name,
        credit,
        year_semester,
        compulsory_elective,
        restrict,
        select,
        selected,
        remaining,
        teacher,
        room,
    ) = values[:17]
    class_time = values[17:24]

    description_cell = cells[24]
    tags: list[str] = []
    for tag in description_cell.select("font"):
        tags.append(tag.get_text(strip=True))
        tag.extract()
    description = description_cell.get_text(strip=True)
    english = "※英語授課" in description
    description = description.replace("※英語授課", "").strip()

    link = cells[7].select_one("small a[href]") or cells[7].select_one("a[href]")
    if link is None:
        raise ValueError("course outline URL is missing")
    if change not in {"", "異動", "新增"}:
        raise ValueError(f"unexpected change marker: {change!r}")
    if multiple_compulsory not in {"", "*"}:
        raise ValueError(f"unexpected multiple-compulsory marker: {multiple_compulsory!r}")
    if not course_id or not name or not grade or not credit:
        raise ValueError("course identity, grade, or credit is empty")
    if year_semester not in {"年", "期"}:
        raise ValueError(f"unexpected year/semester marker: {year_semester!r}")
    if compulsory_elective not in {"必", "選"}:
        raise ValueError(f"unexpected compulsory marker: {compulsory_elective!r}")

    return {
        "url": urljoin(OFFICIAL_BASE_URL + "/", str(link.get("href") or "")),
        "change": _optional(change),
        "changeDescription": _optional(change_description),
        "multipleCompulsory": multiple_compulsory == "*",
        "department": department,
        "id": course_id,
        "grade": grade,
        "class": _optional(course_class),
        "name": name,
        "credit": credit,
        "yearSemester": year_semester,
        "compulsory": compulsory_elective == "必",
        "restrict": _integer(restrict, "restrict"),
        "select": _integer(select, "select"),
        "selected": _integer(selected, "selected"),
        "remaining": _integer(remaining, "remaining"),
        "teacher": teacher,
        "room": room,
        "classTime": class_time,
        "description": description,
        "tags": tags,
        "english": english,
    }


def parse_course_pages(pages: list[str]) -> list[dict[str, Any]]:
    courses: list[dict[str, Any]] = []
    errors: list[str] = []
    for page_number, page in enumerate(pages, start=1):
        soup = BeautifulSoup(page, "html.parser")
        rows = soup.select("table tr[bgcolor]")
        if not rows:
            errors.append(f"page {page_number} contained no course rows")
            continue
        for row_number, row in enumerate(rows, start=1):
            try:
                courses.append(parse_course_row(row))
            except ValueError as error:
                errors.append(f"page {page_number} row {row_number}: {error}")
    if errors:
        preview = "; ".join(errors[:10])
        if len(errors) > 10:
            preview += f"; ... {len(errors) - 10} more"
        raise DirectCrawlError(f"official course table parsing was incomplete: {preview}")
    return courses


def _query_payload(semester: str, captcha: str) -> dict[str, str]:
    return {
        "HIS": "",
        "IDNO": "",
        "ITEM": "",
        "D0": semester,
        "DEG_COD": "*",
        "D1": "",
        "D2": "",
        "CLASS_COD": "",
        "SECT_COD": "",
        "TYP": "1",
        "SDG_COD": "",
        "teacher": "",
        "crsname": "",
        "T3": "",
        "WKDAY": "",
        "SECT": "",
        "nowhis": "1",
        "ValidCode": captcha,
    }


async def _request_text(
    session: aiohttp.ClientSession,
    method: str,
    url: str,
    *,
    data: dict[str, str] | None = None,
    semaphore: asyncio.Semaphore | None = None,
) -> str:
    last_error: Exception | None = None
    for attempt in range(MAX_REQUEST_ATTEMPTS):
        try:
            if semaphore is None:
                async with session.request(method, url, data=data) as response:
                    response.raise_for_status()
                    return await response.text(errors="replace")
            async with semaphore:
                async with session.request(method, url, data=data) as response:
                    response.raise_for_status()
                    return await response.text(errors="replace")
        except (aiohttp.ClientError, asyncio.TimeoutError) as error:
            last_error = error
            if attempt + 1 < MAX_REQUEST_ATTEMPTS:
                await asyncio.sleep(2**attempt)
    raise DirectCrawlError(f"official request failed after retries: {url}: {last_error}")


async def _request_bytes(session: aiohttp.ClientSession, url: str) -> bytes:
    last_error: Exception | None = None
    for attempt in range(MAX_REQUEST_ATTEMPTS):
        try:
            async with session.get(url) as response:
                response.raise_for_status()
                return await response.read()
        except (aiohttp.ClientError, asyncio.TimeoutError) as error:
            last_error = error
            if attempt + 1 < MAX_REQUEST_ATTEMPTS:
                await asyncio.sleep(2**attempt)
    raise DirectCrawlError(f"official binary request failed after retries: {url}: {last_error}")


async def _crawl_official_async(
    semester: str | None,
    timeout: float,
    solver: CaptchaSolver,
    max_pages: int | None,
    progress: Callable[[str], None] | None,
) -> OfficialCrawlResult:
    ssl_context = ssl.create_default_context(ssl.Purpose.SERVER_AUTH)
    ssl_context.options |= 0x4  # OpenSSL OP_LEGACY_SERVER_CONNECT
    connector = aiohttp.TCPConnector(ssl=ssl_context, limit=MAX_CONCURRENT_REQUESTS)
    # Campus services can be slow during enrollment. This is a per-request
    # timeout; crawl-level safety comes from the Actions job timeout and the
    # two-request connection limit.
    client_timeout = aiohttp.ClientTimeout(
        # Do not count time waiting for the two-slot connection semaphore as a
        # request timeout when all result pages are queued together.
        total=None,
        connect=min(timeout, 60),
        sock_read=timeout,
    )
    async with aiohttp.ClientSession(
        connector=connector,
        headers=DEFAULT_HEADERS,
        timeout=client_timeout,
    ) as session:
        if progress:
            progress(
                f"requesting semester index (up to {MAX_REQUEST_ATTEMPTS} connection attempts)"
            )
        query_page = await _request_text(session, "GET", OFFICIAL_QUERY_URL)
        current_semester, history = parse_semester_index(query_page)
        target_semester = semester or current_semester
        if progress:
            progress(f"semester index received; current semester is {current_semester}")
        if target_semester != current_semester:
            raise DirectCrawlError(
                f"direct crawl is limited to current semester {current_semester}; "
                f"requested {target_semester} uses the static fallback"
            )

        first_page = ""
        captcha = ""
        for _ in range(MAX_CAPTCHA_ATTEMPTS):
            captcha_url = f"{OFFICIAL_CAPTCHA_URL}?epoch={time.time_ns()}"
            captcha = solver.solve(await _request_bytes(session, captcha_url))
            first_page = await _request_text(
                session,
                "POST",
                f"{OFFICIAL_RESULTS_URL}?page=1",
                data=_query_payload(target_semester, captcha),
            )
            if "Wrong Validation Code" not in first_page:
                break
        else:
            raise DirectCrawlError("official captcha was rejected on every retry")

        matches = PAGE_COUNT_PATTERN.findall(first_page)
        if not matches:
            raise DirectCrawlError("official results did not declare a page count")
        page_count = int(matches[-1])
        if page_count < 1 or page_count > 500:
            raise DirectCrawlError(f"implausible official page count: {page_count}")
        fetch_page_count = min(page_count, max_pages) if max_pages is not None else page_count
        if fetch_page_count < 1:
            raise DirectCrawlError(f"max_pages must be positive, received {max_pages}")
        if progress:
            progress(
                f"official query accepted; fetching {fetch_page_count} page(s) with concurrency 2"
            )

        semaphore = asyncio.Semaphore(MAX_CONCURRENT_REQUESTS)
        payload = _query_payload(target_semester, captcha)
        remaining_pages = await asyncio.gather(
            *[
                _request_text(
                    session,
                    "POST",
                    f"{OFFICIAL_RESULTS_URL}?page={page}",
                    data=payload,
                    semaphore=semaphore,
                )
                for page in range(2, fetch_page_count + 1)
            ]
        )
        pages = [first_page, *remaining_pages]
        if any("Wrong Validation Code" in page for page in pages):
            raise DirectCrawlError("official captcha expired while course pages were fetched")
        courses = parse_course_pages(pages)
        if progress:
            progress(f"parsed {len(courses)} complete course rows from all declared pages")
        return OfficialCrawlResult(
            semester=target_semester,
            semester_history=history,
            courses=courses,
            page_count=fetch_page_count,
            retrieved_at=datetime.now(timezone.utc),
        )


def crawl_official_courses(
    *,
    semester: str | None = None,
    timeout: float = 45.0,
    max_duration: float = 720.0,
    max_pages: int | None = None,
    model_path: str | None = None,
    progress: Callable[[str], None] | None = None,
) -> OfficialCrawlResult:
    solver = CaptchaSolver(model_path)
    try:
        return asyncio.run(
            asyncio.wait_for(
                _crawl_official_async(semester, timeout, solver, max_pages, progress),
                timeout=max_duration,
            )
        )
    except TimeoutError as error:
        raise DirectCrawlError(
            f"official crawl exceeded its {max_duration:g}-second budget"
        ) from error
    except DirectCrawlError:
        raise
    except Exception as error:
        raise DirectCrawlError(f"official crawl failed: {error}") from error
