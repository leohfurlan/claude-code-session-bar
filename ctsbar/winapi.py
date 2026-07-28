"""Envelopes finos de ctypes sobre a API do Windows.

Tudo aqui e best-effort: em qualquer sistema que nao seja Windows, ou se uma
chamada falhar, as funcoes devolvem None/False e o app segue no caminho
generico. Nenhuma delas exige privilegio de administrador.
"""

from __future__ import annotations

import ctypes
import sys
from typing import Optional, Tuple

IS_WINDOWS = sys.platform == "win32"

SPI_GETWORKAREA = 0x0030
GWL_EXSTYLE = -20
WS_EX_TOOLWINDOW = 0x00000080
WS_EX_NOACTIVATE = 0x08000000
SWP_NOACTIVATE = 0x0010
SWP_NOZORDER = 0x0004
SWP_SHOWWINDOW = 0x0040


class RECT(ctypes.Structure):
    _fields_ = [
        ("left", ctypes.c_long),
        ("top", ctypes.c_long),
        ("right", ctypes.c_long),
        ("bottom", ctypes.c_long),
    ]


def enable_dpi_awareness() -> None:
    """Evita a barra sair borrada ou fora de lugar em telas com escala."""
    if not IS_WINDOWS:
        return
    try:
        # Per-monitor v2 quando disponivel (Windows 10 1703+).
        ctypes.windll.user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4))
        return
    except Exception:
        pass
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(1)
    except Exception:
        try:
            ctypes.windll.user32.SetProcessDPIAware()
        except Exception:
            pass


def work_area() -> Optional[Tuple[int, int, int, int]]:
    """Area util da tela primaria (exclui a taskbar), como (l, t, r, b).

    O `bottom` daqui e exatamente a borda de cima da taskbar quando ela esta
    embaixo — que e onde encostamos a barra.
    """
    if not IS_WINDOWS:
        return None
    rect = RECT()
    try:
        ok = ctypes.windll.user32.SystemParametersInfoW(
            SPI_GETWORKAREA, 0, ctypes.byref(rect), 0
        )
    except Exception:
        return None
    if not ok:
        return None
    return rect.left, rect.top, rect.right, rect.bottom


def screen_size() -> Optional[Tuple[int, int]]:
    if not IS_WINDOWS:
        return None
    try:
        user32 = ctypes.windll.user32
        return user32.GetSystemMetrics(0), user32.GetSystemMetrics(1)
    except Exception:
        return None


def find_window(class_name: Optional[str], window_name: Optional[str] = None) -> Optional[int]:
    if not IS_WINDOWS:
        return None
    try:
        hwnd = ctypes.windll.user32.FindWindowW(class_name, window_name)
    except Exception:
        return None
    return hwnd or None


def find_child_window(parent: int, class_name: str) -> Optional[int]:
    if not IS_WINDOWS:
        return None
    try:
        hwnd = ctypes.windll.user32.FindWindowExW(parent, 0, class_name, None)
    except Exception:
        return None
    return hwnd or None


def get_window_rect(hwnd: int) -> Optional[Tuple[int, int, int, int]]:
    if not IS_WINDOWS:
        return None
    rect = RECT()
    try:
        if not ctypes.windll.user32.GetWindowRect(hwnd, ctypes.byref(rect)):
            return None
    except Exception:
        return None
    return rect.left, rect.top, rect.right, rect.bottom


def make_tool_window(hwnd: int) -> bool:
    """Tira a janela do Alt+Tab e impede que ela roube o foco."""
    if not IS_WINDOWS:
        return False
    try:
        user32 = ctypes.windll.user32
        style = user32.GetWindowLongW(hwnd, GWL_EXSTYLE)
        user32.SetWindowLongW(hwnd, GWL_EXSTYLE, style | WS_EX_TOOLWINDOW | WS_EX_NOACTIVATE)
        return True
    except Exception:
        return False


def set_parent(child: int, parent: int) -> bool:
    if not IS_WINDOWS:
        return False
    try:
        return bool(ctypes.windll.user32.SetParent(child, parent))
    except Exception:
        return False


def move_window(hwnd: int, x: int, y: int, width: int, height: int) -> bool:
    if not IS_WINDOWS:
        return False
    try:
        return bool(
            ctypes.windll.user32.SetWindowPos(
                hwnd, 0, x, y, width, height, SWP_NOZORDER | SWP_NOACTIVATE | SWP_SHOWWINDOW
            )
        )
    except Exception:
        return False
