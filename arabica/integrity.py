"""Model integrity: SHA-256 of the bundled GGUF against models/manifest.json (fail closed).

Hashing 7–11 GB takes ~10–60 s, so a successful result is cached in %LOCALAPPDATA%\\ArabicaAIPro\\verified.json
keyed by (path, size, mtime). Any change of the file triggers re-verification.
"""
from __future__ import annotations

import hashlib
import json
import threading
from pathlib import Path
from typing import Callable

from .backend import BackendError, Cancelled
from .config import data_root, load_manifest, model_file


def _cache_path() -> Path:
    return data_root() / "verified.json"


def _key(p: Path) -> str:
    st = p.stat()
    return f"{p.resolve()}|{st.st_size}|{st.st_mtime_ns}"


def verify_model(progress: Callable[[float], None] | None = None,
                 cancel: threading.Event | None = None, force: bool = False) -> dict:
    manifest = load_manifest()
    p = model_file()
    if not manifest:
        raise BackendError("Не найден файл описания модели (models/manifest.json). Переустановите программу.")
    if not p.is_file():
        raise BackendError(f"Не найден файл модели {p.name}. Переустановите программу.")
    expected = manifest.get("sha256", "").lower()
    size = manifest.get("size")
    if size and p.stat().st_size != size:
        raise BackendError("Файл модели повреждён (размер не совпадает). Переустановите программу "
                           "или заново скопируйте части установщика и проверьте SHA256SUMS.txt.")
    key = _key(p)
    if not force:
        try:
            cache = json.loads(_cache_path().read_text(encoding="utf-8"))
            if cache.get(key) == expected:
                return {"verified": True, "cached": True, "sha256": expected}
        except (OSError, ValueError):
            pass
    h = hashlib.sha256()
    total = p.stat().st_size or 1
    done = 0
    with p.open("rb") as f:
        while True:
            if cancel and cancel.is_set():
                raise Cancelled("Проверка модели отменена.")
            b = f.read(16 << 20)
            if not b:
                break
            h.update(b)
            done += len(b)
            if progress:
                progress(done / total)
    digest = h.hexdigest()
    if expected and digest != expected:
        raise BackendError("Контрольная сумма модели не совпадает — файл повреждён. Переустановите программу "
                           "или заново скопируйте части установщика и проверьте SHA256SUMS.txt.")
    try:
        _cache_path().write_text(json.dumps({key: digest}), encoding="utf-8")
    except OSError:
        pass
    return {"verified": bool(expected), "cached": False, "sha256": digest}
