import os
import sys
from pathlib import Path

from . import APP_NAME


def app_dir() -> Path:
    override = os.environ.get("MAIL2DISK_HOME")
    if override:
        base = Path(override)
    elif sys.platform == "win32":
        base = Path(os.environ.get("APPDATA") or Path.home() / "AppData" / "Roaming") / APP_NAME
    elif sys.platform == "darwin":
        base = Path.home() / "Library" / "Application Support" / APP_NAME
    else:
        base = Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config") / APP_NAME
    base.mkdir(parents=True, exist_ok=True)
    return base


def config_path() -> Path:
    return app_dir() / "config.json"


def state_path() -> Path:
    return app_dir() / "state.json"


def log_path() -> Path:
    return app_dir() / "log.txt"


def lock_path() -> Path:
    return app_dir() / "run.lock"
