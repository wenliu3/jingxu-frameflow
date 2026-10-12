"""Publish complete local records without truncating the previous version."""
from __future__ import annotations

import json
import os
from pathlib import Path
import tempfile
import threading
from typing import Any

# Windows can reject simultaneous replacements of the same destination. Only
# serialize the short publish step; preparation and flushing remain concurrent.
_REPLACE_LOCK = threading.Lock()


def atomic_write_text(path: str | Path, content: str) -> None:
    path = Path(path)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", dir=path.parent,
            prefix=f".{path.name}-", suffix=".tmp", delete=False,
        ) as handle:
            temporary = Path(handle.name)
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        with _REPLACE_LOCK:
            os.replace(temporary, path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def atomic_write_json(path: str | Path, value: Any) -> None:
    atomic_write_text(path, json.dumps(value, ensure_ascii=False, indent=2))
