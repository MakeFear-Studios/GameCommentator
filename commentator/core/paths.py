"""Where config and logs live, from source and from the packaged .exe."""

import sys
from pathlib import Path


def app_root() -> Path:
    """The repo root from source, or the bundle folder when frozen by PyInstaller."""
    if getattr(sys, "frozen", False):
        return Path(getattr(sys, "_MEIPASS", Path(sys.executable).parent))
    return Path(__file__).resolve().parents[2]


def config_dir() -> Path:
    return app_root() / "config"


def games_dir() -> Path:
    return config_dir() / "games"


def logs_dir() -> Path:
    return app_root() / "logs"
