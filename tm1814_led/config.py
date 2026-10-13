"""Where things live and how the process is configured (environment variables and defaults)."""
from __future__ import annotations

import os
import platform
from pathlib import Path

APP_NAME = "tm1814-led"
DEFAULT_HOST = "0.0.0.0"
DEFAULT_PORT = 8080


def default_data_dir() -> Path:
    """Data directory: $TM1814_DATA_DIR, else a per-user location for this operating system."""
    env = os.environ.get("TM1814_DATA_DIR")
    if env:
        return Path(env).expanduser()
    system = platform.system()
    if system == "Darwin":
        return Path.home() / "Library" / "Application Support" / APP_NAME
    if system == "Windows":
        return Path(os.environ.get("LOCALAPPDATA", str(Path.home() / "AppData" / "Local"))) / APP_NAME
    return Path(os.environ.get("XDG_DATA_HOME", str(Path.home() / ".local" / "share"))) / APP_NAME


def env_flag(name: str) -> bool:
    return os.environ.get(name, "").strip().lower() in ("1", "true", "yes", "on")
