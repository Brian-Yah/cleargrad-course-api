from __future__ import annotations

import hashlib
import json
from pathlib import Path
import time
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


class FetchError(RuntimeError):
    """Raised when all configured static sources fail."""


def canonical_json_bytes(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")


def sha256_json(value: Any) -> str:
    return hashlib.sha256(canonical_json_bytes(value)).hexdigest()


def read_json(path: Path, default: Any = None) -> Any:
    if not path.is_file():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return default


def write_json(path: Path, value: Any, *, pretty: bool = False) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if pretty:
        payload = json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True)
    else:
        payload = json.dumps(value, ensure_ascii=False, separators=(",", ":"))
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(payload + ("\n" if pretty else ""), encoding="utf-8")
    temporary.replace(path)


def fetch_bytes(url: str, *, timeout: float = 45.0, attempts: int = 3) -> bytes:
    last_error: Exception | None = None
    request = Request(
        url,
        headers={
            "Accept": "application/json, text/plain;q=0.9, */*;q=0.1",
            "User-Agent": "ClearGrad-Course-API/0.1 (+https://github.com/Brian-Yah)",
        },
    )
    for attempt in range(attempts):
        try:
            with urlopen(request, timeout=timeout) as response:
                return response.read()
        except (HTTPError, URLError, TimeoutError, OSError) as error:
            last_error = error
            if attempt + 1 < attempts:
                time.sleep(2**attempt)
    raise FetchError(f"fetch failed after {attempts} attempts: {url}: {last_error}")


def fetch_json(url: str, *, timeout: float = 45.0, attempts: int = 3) -> Any:
    try:
        return json.loads(fetch_bytes(url, timeout=timeout, attempts=attempts))
    except json.JSONDecodeError as error:
        raise FetchError(f"invalid JSON from {url}: {error}") from error


def join_url(base: str, *parts: str) -> str:
    clean = [str(part).strip("/") for part in parts]
    return "/".join([base.rstrip("/"), *clean])

