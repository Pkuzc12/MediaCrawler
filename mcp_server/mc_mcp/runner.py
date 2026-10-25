"""Bootstrap executed with MediaCrawler's own Python environment.

The server sends a JSON spec over stdin so keywords and targets never appear in
the process command line. The spec's `config` entries are assigned onto
MediaCrawler's `config` package before `main.py` runs, which covers options the
upstream CLI does not expose (search order, date ranges, zhihu creators).
This module intentionally uses only the standard library.
"""

from __future__ import annotations

import json
import os
import runpy
import sys
from pathlib import Path
from typing import Any


def _read_spec() -> dict[str, Any]:
    raw = sys.stdin.buffer.readline(256 * 1024 + 1)
    if not raw or len(raw) > 256 * 1024:
        raise RuntimeError("Missing or oversized launch specification")
    value = json.loads(raw.decode("utf-8"))
    if not isinstance(value, dict):
        raise TypeError("Invalid launch specification")
    return value


def main() -> None:
    spec = _read_spec()
    root = Path(str(spec.get("root", ""))).expanduser().resolve()
    main_file = root / "main.py"
    if not main_file.is_file():
        raise RuntimeError("MediaCrawler main.py is unavailable")
    overrides = spec.get("config") or {}
    if not isinstance(overrides, dict):
        raise RuntimeError("Invalid config overrides")

    os.chdir(root)
    sys.path.insert(0, str(root))
    import config  # type: ignore[import-not-found]

    for name, value in overrides.items():
        if not name.isupper() or not hasattr(config, name):
            raise RuntimeError(f"Unknown MediaCrawler config option: {name}")
        setattr(config, name, value)

    sys.argv[0] = str(main_file)
    runpy.run_path(str(main_file), run_name="__main__")


if __name__ == "__main__":
    main()
