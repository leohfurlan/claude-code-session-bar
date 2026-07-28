"""Modo experimental: embutir a barra dentro da taskbar de verdade.

A taskbar do Windows e a janela `Shell_TrayWnd`. Da pra reparentar uma janela
propria pra dentro dela com SetParent, e e isso que este modulo faz.

Por que fica desligado por padrao:

* a arvore de janelas do shell muda entre versoes do Windows 11, entao a
  posicao calculada pode nao bater;
* o Explorer redesenha a taskbar em varios eventos (troca de DPI, mostrar area
  de trabalho, reinicio do explorer.exe) e a janela filha some junto;
* nao existe API suportada pra isso — e um detalhe de implementacao do shell.

Se `taskbar_embed` estiver ligado e algo falhar, `try_embed` devolve False e a
barra continua no modo flutuante, que e o caminho confiavel.
"""

from __future__ import annotations

from typing import Optional, Tuple

from ..winapi import (
    IS_WINDOWS,
    find_child_window,
    find_window,
    get_window_rect,
    move_window,
    set_parent,
)


def find_taskbar() -> Optional[int]:
    return find_window("Shell_TrayWnd")


def find_tray_area() -> Optional[int]:
    """Area de notificacao (relogio + icones), no canto direito da taskbar."""
    taskbar = find_taskbar()
    if taskbar is None:
        return None
    return find_child_window(taskbar, "TrayNotifyWnd")


def compute_slot(width: int, height: int) -> Optional[Tuple[int, int]]:
    """Posicao (x, y) dentro da taskbar, logo a esquerda da area de notificacao.

    Coordenadas relativas a taskbar, que e o que SetWindowPos espera depois do
    reparent.
    """
    taskbar = find_taskbar()
    if taskbar is None:
        return None
    taskbar_rect = get_window_rect(taskbar)
    if taskbar_rect is None:
        return None

    tb_left, tb_top, tb_right, tb_bottom = taskbar_rect
    taskbar_height = tb_bottom - tb_top

    tray = find_tray_area()
    tray_rect = get_window_rect(tray) if tray is not None else None
    # Sem a area de notificacao localizada, encosta na borda direita.
    right_edge = (tray_rect[0] if tray_rect else tb_right) - tb_left

    x = max(0, right_edge - width - 8)
    y = max(0, (taskbar_height - height) // 2)
    return x, y


def try_embed(hwnd: int, width: int, height: int) -> bool:
    """Reparenta `hwnd` pra dentro da taskbar. False se nao rolar."""
    if not IS_WINDOWS:
        return False

    taskbar = find_taskbar()
    if taskbar is None:
        return False

    slot = compute_slot(width, height)
    if slot is None:
        return False

    if not set_parent(hwnd, taskbar):
        return False

    x, y = slot
    if not move_window(hwnd, x, y, width, height):
        # Reparentou mas nao posicionou: desfaz pra nao deixar janela perdida.
        set_parent(hwnd, 0)
        return False
    return True


def undo_embed(hwnd: int) -> bool:
    """Devolve a janela pro desktop (parent = NULL)."""
    if not IS_WINDOWS:
        return False
    return set_parent(hwnd, 0)
