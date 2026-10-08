from __future__ import annotations
import argparse
import sys
from .backend import EngineConfig, check_files, BackendError
from .config import model_file, runtime_file


def main():
    parser = argparse.ArgumentParser(prog="arabica")
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()
    if args.self_test:
        try:
            check_files(EngineConfig(runtime_file(),model_file()))
            print("OK: локальная GGUF-модель и движок найдены. Реальный перевод не проверен.")
            return 0
        except BackendError as ex:
            print("НЕ ГОТОВО:", ex)
            return 2
    try:
        from .gui import main as gui_main
    except ImportError:
        print("PySide6 не установлен в текущей среде разработки.", file=sys.stderr)
        return 1
    return gui_main()


if __name__ == "__main__":
    raise SystemExit(main())
