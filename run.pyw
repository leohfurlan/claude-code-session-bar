"""Ponto de entrada para rodar em background no Windows.

A extensao .pyw faz o Windows abrir com pythonw.exe, sem janela de console.
E este o arquivo apontado pelo inicio automatico (veja ctsbar/autostart.py).
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from ctsbar.__main__ import main  # noqa: E402

if __name__ == "__main__":
    sys.exit(main([]))
