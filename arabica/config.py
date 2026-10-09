"""Application paths and the bundled model manifest. Never fetches or downloads anything."""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path


def app_root() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parents[1]


def data_root() -> Path:
    local = os.environ.get("LOCALAPPDATA")
    p = Path(local) / "ArabicaAIPro" if local else Path.home() / ".arabica-ai-pro"
    p.mkdir(parents=True, exist_ok=True)
    return p


def runtime_dir() -> Path:
    return app_root() / "runtime"


def runtime_file() -> Path:
    exe = "llama-server.exe" if sys.platform == "win32" else "llama-server"
    return runtime_dir() / exe


def manifest_file() -> Path:
    return app_root() / "models" / "manifest.json"


def load_manifest() -> dict:
    """models/manifest.json written at build time: file name, sha256, size, source, licence, params."""
    p = manifest_file()
    if not p.is_file():
        return {}
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def model_file() -> Path:
    name = load_manifest().get("file", "translator.gguf")
    return app_root() / "models" / name


def database_file() -> Path:
    return data_root() / "arabica.sqlite3"


def log_dir() -> Path:
    p = data_root() / "logs"
    p.mkdir(parents=True, exist_ok=True)
    return p
