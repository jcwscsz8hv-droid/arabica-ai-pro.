"""Application wiring: build the engine from the bundled manifest, self-test, environment facts."""
from __future__ import annotations

import json
import os
import platform
import re
import sys
import threading
import time
from datetime import datetime
from pathlib import Path

from . import __version__
from .backend import BackendError, EngineConfig, LlamaServerEngine, physical_cores
from .config import data_root, load_manifest, log_dir, model_file, runtime_file


def available_ram_gb() -> float | None:
    try:
        import psutil
        return psutil.virtual_memory().available / 1e9
    except Exception:  # noqa: BLE001
        return None


def choose_context(manifest: dict) -> int:
    """8192 tokens by default; 4096 if memory is tight (model size + ~3 GB headroom)."""
    ctx = int(manifest.get("context_tokens", 8192))
    avail = available_ram_gb()
    size_gb = (manifest.get("size") or 8e9) / 1e9
    if avail is not None and avail < size_gb + 3:
        ctx = min(ctx, 4096)
    return ctx


def make_engine() -> LlamaServerEngine:
    m = load_manifest()
    cfg = EngineConfig(runtime_file(), model_file(), threads=int(m.get("threads", 0)),
                       context_tokens=choose_context(m), max_tokens=int(m.get("max_tokens", 2048)))
    return LlamaServerEngine(cfg, log_path=log_dir() / "engine.log")


def model_label() -> str:
    m = load_manifest()
    return m.get("display_name") or m.get("file", "модель")


def cpu_backend_from_log(log: Path) -> str:
    try:
        text = log.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""
    m = re.findall(r"ggml-cpu-([a-z0-9]+)\.dll", text)
    return m[-1] if m else ""


def check_memory_or_raise() -> None:
    m = load_manifest()
    avail = available_ram_gb()
    need = (m.get("size") or 0) / 1e9 + 1.0
    if avail is not None and need > 1 and avail < need:
        raise BackendError(f"Недостаточно свободной оперативной памяти: доступно {avail:.1f} ГБ, "
                           f"нужно не менее {need:.1f} ГБ. Закройте другие программы.")


SELFTEST_CASES = [
    ("ru-ar", "Министр иностранных дел заявил, что переговоры не будут возобновлены до 15 марта 2027 года."),
    ("ar-ru", "لم يحضر الوفد الاجتماع بسبب الظروف الجوية."),
]


def self_test(progress=None, cancel: threading.Event | None = None) -> dict:
    """Offline readiness test: files, integrity, engine start, one real translation per direction."""
    from .integrity import verify_model
    from .service import Translator

    say = progress or (lambda s: None)
    rep: dict = {"app_version": __version__, "time": datetime.now().isoformat(timespec="seconds"),
                 "os": platform.platform(), "machine": platform.machine(), "cpu": platform.processor(),
                 "cpu_cores_physical": physical_cores(), "cpu_logical": os.cpu_count(),
                 "ram_available_gb": round(available_ram_gb() or 0, 1), "frozen": bool(getattr(sys, "frozen", False)),
                 "app_dir": str(Path(sys.executable).parent if getattr(sys, "frozen", False) else Path.cwd()),
                 "model": load_manifest(), "steps": [], "ok": False}
    try:
        import psutil
        rep["ram_total_gb"] = round(psutil.virtual_memory().total / 1e9, 1)
    except Exception:  # noqa: BLE001
        pass
    eng = None
    try:
        say("Проверка контрольной суммы модели…")
        t = time.time()
        v = verify_model(cancel=cancel)
        rep["steps"].append({"step": "model_sha256", "ok": True, "seconds": round(time.time() - t, 1), **v})
        check_memory_or_raise()
        say("Загрузка модели…")
        eng = make_engine()
        t = time.time()
        eng.start(cancel)
        rep["steps"].append({"step": "engine_start", "ok": True, "seconds": round(time.time() - t, 1),
                             "context_tokens": eng.config.context_tokens})
        tr = Translator(eng)
        for d, src in SELFTEST_CASES:
            say(f"Пробный перевод {d}…")
            t = time.time()
            r = tr.translate(src, d, "accurate", cancel=cancel)
            rep["steps"].append({"step": f"translate_{d}", "ok": bool(r.text), "seconds": round(time.time() - t, 1),
                                 "source": src, "output": r.text, "warnings": [w.message for w in r.warnings]})
        try:
            import psutil
            rep["engine_rss_gb"] = round(psutil.Process(eng.proc.pid).memory_info().rss / 1e9, 2)
        except Exception:  # noqa: BLE001
            pass
        rep["ok"] = all(s["ok"] for s in rep["steps"])
    except BackendError as ex:
        rep["steps"].append({"step": "error", "ok": False, "message": str(ex)})
    finally:
        if eng:
            eng.stop()
            rep["cpu_backend"] = cpu_backend_from_log(eng.log_path)
    path = data_root() / f"selftest-{datetime.now():%Y%m%d-%H%M%S}.txt"
    path.write_text(format_report(rep), encoding="utf-8")
    rep["report_path"] = str(path)
    return rep


def format_report(rep: dict) -> str:
    lines = ["ARABICA AI PRO — САМОПРОВЕРКА (без сети)", "=" * 44,
             f"Итог: {'ГОТОВО — перевод работает' if rep.get('ok') else 'НЕ ГОТОВО'}",
             f"Версия программы: {rep.get('app_version')}   Время: {rep.get('time')}",
             f"Windows: {rep.get('os')}", f"Процессор: {rep.get('cpu')} · ядер {rep.get('cpu_cores_physical')}"
             f" (логических {rep.get('cpu_logical')}) · вариант движка: {rep.get('cpu_backend') or '?'}",
             f"Память: всего {rep.get('ram_total_gb')} ГБ, свободно {rep.get('ram_available_gb')} ГБ, "
             f"занято моделью {rep.get('engine_rss_gb')} ГБ",
             f"Модель: {rep.get('model', {}).get('display_name')} · SHA-256 {str(rep.get('model', {}).get('sha256'))[:16]}…",
             ""]
    for s in rep.get("steps", []):
        mark = "OK " if s.get("ok") else "ERR"
        line = f"[{mark}] {s['step']}"
        if "seconds" in s:
            line += f" · {s['seconds']} с"
        lines.append(line)
        if s.get("output"):
            lines += [f"      {s['source']}", f"   →  {s['output']}"]
        if s.get("warnings"):
            lines.append("      замечания: " + "; ".join(s["warnings"]))
        if s.get("message"):
            lines.append("      " + s["message"])
    return "\n".join(lines) + "\n\n" + json.dumps(rep, ensure_ascii=False, indent=1, default=str) + "\n"
