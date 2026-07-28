"""Liga/desliga o inicio automatico com o Windows.

Usa `HKEY_CURRENT_USER\\Software\\Microsoft\\Windows\\CurrentVersion\\Run`, que
e por usuario e nao pede administrador.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Optional

RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
VALUE_NAME = "ClaudeCodeSessionBar"


def _winreg():
    try:
        import winreg  # noqa: PLC0415 — so existe no Windows
    except ImportError:
        return None
    return winreg


def launcher_path() -> Path:
    """Caminho do run.pyw na raiz do projeto."""
    return Path(__file__).resolve().parent.parent / "run.pyw"


def pythonw_executable() -> str:
    """pythonw.exe roda sem abrir janela de console."""
    executable = Path(sys.executable)
    candidate = executable.with_name("pythonw.exe")
    return str(candidate if candidate.exists() else executable)


def command_line() -> str:
    return f'"{pythonw_executable()}" "{launcher_path()}"'


def is_enabled() -> bool:
    winreg = _winreg()
    if winreg is None:
        return False
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as key:
            value, _ = winreg.QueryValueEx(key, VALUE_NAME)
            return bool(value)
    except OSError:
        return False


def enable() -> bool:
    winreg = _winreg()
    if winreg is None:
        return False
    try:
        with winreg.CreateKeyEx(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_SET_VALUE) as key:
            winreg.SetValueEx(key, VALUE_NAME, 0, winreg.REG_SZ, command_line())
        return True
    except OSError:
        return False


def disable() -> bool:
    winreg = _winreg()
    if winreg is None:
        return False
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_SET_VALUE) as key:
            winreg.DeleteValue(key, VALUE_NAME)
        return True
    except FileNotFoundError:
        return True  # ja estava desligado
    except OSError:
        return False


def toggle() -> bool:
    """Inverte o estado e devolve o estado final."""
    if is_enabled():
        disable()
        return False
    enable()
    return is_enabled()


def status_text() -> Optional[str]:
    if _winreg() is None:
        return None
    return "ligado" if is_enabled() else "desligado"
