"""Application paths. Never fetch or download assets."""
from __future__ import annotations
import os
from pathlib import Path
import sys


def app_root() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parents[1]


def data_root() -> Path:
    local = os.environ.get("LOCALAPPDATA")
    if local:
        p = Path(local) / "ArabicaAIPro"
    else:
        p = Path.home() / ".arabica-ai-pro"
    p.mkdir(parents=True, exist_ok=True)
    return p


def model_file() -> Path:
    return app_root() / "models" / "translator.gguf"


def runtime_file() -> Path:
    exe = "llama-cli.exe" if sys.platform == "win32" else "llama-cli"
    return app_root() / "runtime" / exe


def database_file() -> Path:
    return data_root() / "arabica.sqlite3"
