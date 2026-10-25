from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

# mcp_server/mc_mcp/settings.py -> the MediaCrawler checkout is two levels up.
_DEFAULT_ROOT = Path(__file__).resolve().parents[2]


@dataclass(frozen=True, slots=True)
class Settings:
    mediacrawler_root: Path
    state_dir: Path
    python_executable: Path
    cdp_port: int = 9222

    @classmethod
    def from_env(cls) -> Settings:
        root_value = os.environ.get("MEDIACRAWLER_ROOT", "").strip()
        root = Path(root_value).expanduser() if root_value else _DEFAULT_ROOT

        state_value = os.environ.get("MC_MCP_STATE_DIR", "").strip()
        state_dir = (
            Path(state_value).expanduser()
            if state_value
            else root / "data" / "mcp_runs"
        )

        python_value = os.environ.get("MEDIACRAWLER_PYTHON", "").strip()
        if python_value:
            python_executable = Path(python_value).expanduser()
        elif os.name == "nt":
            python_executable = root / ".venv" / "Scripts" / "python.exe"
        else:
            python_executable = root / ".venv" / "bin" / "python"

        port_value = os.environ.get("MC_MCP_CDP_PORT", "").strip()
        cdp_port = int(port_value) if port_value else 9222
        return cls(root, state_dir, python_executable, cdp_port)

    @property
    def main_file(self) -> Path:
        return self.mediacrawler_root / "main.py"
