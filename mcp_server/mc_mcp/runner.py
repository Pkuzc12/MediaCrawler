"""Bootstrap executed with MediaCrawler's own Python environment.

The server sends a JSON spec over stdin so keywords and targets never appear in
the process command line. The spec's `config` entries are assigned onto
MediaCrawler's `config` package before `main.py` runs, which covers options the
upstream CLI does not expose (search order, date ranges, zhihu creators).

When the CDP connection fails, every upstream crawler silently falls back to
launching Playwright's bundled Chromium, which is not installed and hides the
real problem; the runner turns that fallback into a clear error instead.
Apart from that Playwright hook this module uses only the standard library.
"""

from __future__ import annotations

import json
import os
import runpy
import sys
from pathlib import Path
from typing import Any

NO_FALLBACK_MESSAGE = (
    "mc_mcp refuses to launch Playwright's bundled browser: the CDP connection to "
    "the user's Chrome failed (remote debugging off, or the Allow dialog was not accepted)."
)


def _read_spec() -> dict[str, Any]:
    raw = sys.stdin.buffer.readline(256 * 1024 + 1)
    if not raw or len(raw) > 256 * 1024:
        raise RuntimeError("Missing or oversized launch specification")
    value = json.loads(raw.decode("utf-8"))
    if not isinstance(value, dict):
        raise TypeError("Invalid launch specification")
    return value


def _block_bundled_browser() -> None:
    try:
        from playwright.async_api import BrowserType  # type: ignore[import-not-found]
    except ImportError:
        return

    async def refuse(*_: Any, **__: Any) -> Any:
        raise RuntimeError(NO_FALLBACK_MESSAGE)

    BrowserType.launch = refuse
    BrowserType.launch_persistent_context = refuse


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
    if overrides.get("CDP_CONNECT_EXISTING"):
        _block_bundled_browser()

    sys.argv[0] = str(main_file)
    runpy.run_path(str(main_file), run_name="__main__")


if __name__ == "__main__":
    main()
