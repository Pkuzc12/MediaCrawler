"""Stand-in for playwright.async_api: just enough for the runner's launch hook."""

from __future__ import annotations


class BrowserType:
    async def launch(self, **options: object) -> object:
        return object()

    async def launch_persistent_context(self, user_data_dir: str, **options: object) -> object:
        return object()
