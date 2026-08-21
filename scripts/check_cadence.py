from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from cleargrad_course_api.cadence import cadence_due


def fetch_state(url: str, timeout: float = 15.0) -> dict[str, Any] | None:
    request = Request(url, headers={"User-Agent": "ClearGrad-Course-API-Cadence/1.0"})
    try:
        with urlopen(request, timeout=timeout) as response:
            value = json.load(response)
    except (HTTPError, URLError, TimeoutError, json.JSONDecodeError):
        return None
    return value if isinstance(value, dict) else None


def write_output(name: str, value: str) -> None:
    output_path = os.environ.get("GITHUB_OUTPUT")
    if not output_path:
        return
    with Path(output_path).open("a", encoding="utf-8") as output:
        output.write(f"{name}={value}\n")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", required=True)
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args(argv)

    state = fetch_state(args.url)
    due, reason = cadence_due(state)
    if args.force:
        due, reason = True, "manual_dispatch"
    mode = str(state.get("mode") or "unknown") if state else "unknown"
    payload = {
        "due": due,
        "reason": reason,
        "mode": mode,
        "nextFullSyncAt": state.get("nextFullSyncAt") if state else None,
    }
    print(json.dumps(payload, ensure_ascii=False))
    write_output("due", "true" if due else "false")
    write_output("reason", reason)
    write_output("mode", mode)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
