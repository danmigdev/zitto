"""Small JSON files: the user's settings, and the helpers the library uses for its own."""

from __future__ import annotations

import json
import os
import time
from pathlib import Path

# Where everything is kept: the folder of the program, or for the app of the Mac one of the user.
HOME = Path(os.environ.get("ZITTO_HOME") or Path(__file__).resolve().parent.parent)
PATH = HOME / "settings.json"


def read(path: Path) -> dict:
    """The content of a JSON file, {} when it is missing or broken."""
    for _ in range(10):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            return data if isinstance(data, dict) else {}
        except PermissionError:
            time.sleep(0.02)  # being replaced by another process
        except (OSError, ValueError):
            return {}
    return {}


def write(path: Path, data: dict) -> None:
    """Replace a JSON file in one step: readers see the old content or the new one."""
    path.parent.mkdir(parents=True, exist_ok=True)
    partial = path.with_name(f"{path.name}.{os.getpid()}.part")
    partial.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    for attempt in range(10):
        try:
            os.replace(partial, path)
            return
        except PermissionError:  # Windows: somebody is reading it
            if attempt == 9:
                partial.unlink(missing_ok=True)
                raise
            time.sleep(0.02)


def load() -> dict:
    return read(PATH)


def save(**changes) -> None:
    write(PATH, load() | changes)
