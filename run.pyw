"""Ponto de entrada para rodar em background no Windows.

A extensao .pyw faz o Windows abrir com pythonw.exe, sem janela de console.
E este o arquivo apontado pelo inicio automatico (veja ctsbar/autostart.py).

Como o pythonw nao tem console, qualquer erro morreria em silencio — a janela
so "pisca e some". Por isso todo crash e gravado em:

    %APPDATA%\\ctsbar\\error.log
"""

import sys
import traceback
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))


def log_crash(exc: BaseException) -> Path:
    from ctsbar.paths import app_config_dir

    log_path = app_config_dir() / "error.log"
    try:
        app_config_dir().mkdir(parents=True, exist_ok=True)
        with log_path.open("a", encoding="utf-8") as handle:
            handle.write(f"\n{'=' * 70}\n{datetime.now():%Y-%m-%d %H:%M:%S}\n")
            handle.write(f"python: {sys.version}\n")
            handle.write(f"executavel: {sys.executable}\n\n")
            traceback.print_exception(type(exc), exc, exc.__traceback__, file=handle)
    except Exception:
        pass
    return log_path


if __name__ == "__main__":
    try:
        from ctsbar.__main__ import main

        sys.exit(main([]))
    except SystemExit:
        raise
    except BaseException as error:  # noqa: BLE001 — e o ultimo recurso
        path = log_crash(error)
        # Sem console, um MessageBox e a unica forma de avisar o usuario.
        try:
            import ctypes

            ctypes.windll.user32.MessageBoxW(
                None,
                f"A barra nao conseguiu iniciar.\n\n{type(error).__name__}: {error}\n\n"
                f"Detalhes em:\n{path}",
                "Claude Code Session Bar",
                0x10,  # MB_ICONERROR
            )
        except Exception:
            pass
        sys.exit(1)
