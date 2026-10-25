from __future__ import annotations

import socket
import sys
from collections.abc import Iterator
from pathlib import Path

import pytest

from mc_mcp.service import CrawlerService
from mc_mcp.settings import Settings

FAKE_ROOT = Path(__file__).resolve().parent / "fake_mediacrawler"


@pytest.fixture
def fake_mediacrawler_root() -> Path:
    return FAKE_ROOT


@pytest.fixture
def listening_port() -> Iterator[int]:
    """A local TCP port standing in for Chrome's remote-debugging port."""
    with socket.socket() as server:
        server.bind(("127.0.0.1", 0))
        server.listen()
        yield server.getsockname()[1]


@pytest.fixture
def settings(tmp_path: Path, listening_port: int) -> Settings:
    return Settings(
        mediacrawler_root=FAKE_ROOT,
        state_dir=tmp_path / "runs with spaces",
        python_executable=Path(sys.executable),
        cdp_port=listening_port,
    )


@pytest.fixture
def service(settings: Settings) -> CrawlerService:
    return CrawlerService(settings)
