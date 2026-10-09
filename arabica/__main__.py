from __future__ import annotations

import argparse
import os
import subprocess
import sys


def main() -> int:
    parser = argparse.ArgumentParser(prog="ArabicaAIPro")
    parser.add_argument("--self-test", action="store_true",
                        help="офлайн-самопроверка: модель, движок, пробный перевод в обе стороны")
    parser.add_argument("--no-open", action="store_true", help="не открывать отчёт в Блокноте")
    args = parser.parse_args()
    if args.self_test:
        from .app import format_report, self_test
        rep = self_test(progress=lambda s: print(s, flush=True) if sys.stdout else None)
        text = format_report(rep)
        try:
            print(text)
        except (OSError, AttributeError, UnicodeEncodeError):
            pass  # windowed build has no console
        if sys.platform == "win32" and not args.no_open:
            subprocess.Popen(["notepad.exe", rep["report_path"]], creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        return 0 if rep["ok"] else 2
    try:
        from .gui import main as gui_main
    except ImportError as ex:
        print("PySide6 не установлен в текущей среде:", ex, file=sys.stderr)
        return 1
    return gui_main()


if __name__ == "__main__":
    os.environ.setdefault("PYTHONUTF8", "1")
    raise SystemExit(main())
