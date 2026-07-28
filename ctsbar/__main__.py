"""Entrada de linha de comando: `python -m ctsbar`.

    python -m ctsbar                  roda a barra + icone da bandeja
    python -m ctsbar --once           imprime um snapshot e sai (diagnostico)
    python -m ctsbar --watch          modo texto no terminal, sem GUI
    python -m ctsbar --enable-autostart / --disable-autostart
    python -m ctsbar --write-config   grava config.json com os defaults
"""

from __future__ import annotations

import argparse
import sys
import time

from . import autostart
from .config import Config
from .models import State, format_duration, level_for
from .monitor import UsageMonitor


def describe(snapshot) -> str:
    if snapshot.has_percent and snapshot.primary is not None:
        level = level_for(snapshot.percent, 50.0, 80.0)
        remaining = snapshot.primary.seconds_to_reset()
        line = (
            f"{snapshot.percent:5.1f}%  {snapshot.primary.label:<28}"
            f"  reseta em {format_duration(remaining):>7}  [{level.value}]"
        )
    elif snapshot.state is State.ERROR:
        line = f"{'--':>6}  erro"
    elif snapshot.tokens:
        line = f"{'--':>6}  ~{snapshot.tokens // 1000}k tokens ponderados na janela"
    else:
        line = f"{'--':>6}  sem sessao ativa"

    line += f"  (fonte: {snapshot.source})"
    if snapshot.detail:
        line += f"\n        {snapshot.detail}"

    for key, window in sorted(snapshot.windows.items()):
        if snapshot.primary is not None and key == snapshot.primary.key:
            continue
        if window.percent is not None:
            line += f"\n        {window.label}: {window.percent:.1f}%"
    return line


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        prog="ctsbar", description="Barra de uso da sessao do Claude Code"
    )
    parser.add_argument("--once", action="store_true", help="imprime um snapshot e sai")
    parser.add_argument("--watch", action="store_true", help="modo texto, sem interface grafica")
    parser.add_argument("--enable-autostart", action="store_true", help="iniciar com o Windows")
    parser.add_argument("--disable-autostart", action="store_true", help="nao iniciar com o Windows")
    parser.add_argument("--write-config", action="store_true", help="grava o config.json padrao")
    args = parser.parse_args(argv)

    config = Config.load()

    if args.write_config:
        ok = config.save()
        print(f"{'Gravado' if ok else 'Falha ao gravar'}: {config.path}")
        return 0 if ok else 1

    if args.enable_autostart or args.disable_autostart:
        if sys.platform != "win32":
            print("Inicio automatico so no Windows.")
            return 1
        ok = autostart.enable() if args.enable_autostart else autostart.disable()
        print(f"Inicio automatico: {autostart.status_text()}")
        if args.enable_autostart and ok:
            print(f"  comando: {autostart.command_line()}")
        return 0 if ok else 1

    if args.once:
        print(describe(UsageMonitor(config).poll()))
        return 0

    if args.watch:
        monitor = UsageMonitor(config)
        try:
            while True:
                print(f"[{time.strftime('%H:%M:%S')}] {describe(monitor.poll())}", flush=True)
                time.sleep(config.poll_seconds)
        except KeyboardInterrupt:
            return 0

    from .app import App

    return App(config).run()


if __name__ == "__main__":
    sys.exit(main())
