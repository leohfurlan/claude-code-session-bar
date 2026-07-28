"""Junta monitor + barra + bandeja num processo so."""

from __future__ import annotations

import os
import subprocess
import sys

from . import autostart
from .config import Config
from .monitor import UsageMonitor
from .ui.bar import UsageBar
from .ui.tray import TRAY_AVAILABLE, TrayIcon


class App:
    def __init__(self, config: Config):
        self.config = config
        self.monitor = UsageMonitor(
            config, on_update=self._on_snapshot, on_refresh_failed=self._on_refresh_failed
        )
        self.bar = UsageBar(config, on_refresh=self.monitor.refresh_now)
        self.tray = None

        if TRAY_AVAILABLE:
            self.tray = TrayIcon(
                config,
                get_snapshot=lambda: self.monitor.snapshot,
                on_refresh=self.monitor.refresh_now,
                on_toggle_bar=self._toggle_bar,
                on_reset_position=self._reset_position,
                on_toggle_autostart=self._toggle_autostart,
                on_open_config=self._open_config,
                on_quit=self.quit,
                is_autostart_enabled=autostart.is_enabled,
                is_bar_visible=lambda: self.bar.visible,
            )

    # ------------------------------------------------------------- acoes

    def _on_snapshot(self, snapshot) -> None:
        # Chamado da thread do monitor: so pode tocar no icone da bandeja,
        # nunca em widget tkinter. A barra puxa o estado pelo proprio loop.
        if self.tray is not None:
            self.tray.update(snapshot)

    def _on_refresh_failed(self, detail: str) -> None:
        """Avisa quando 'Atualizar agora' nao conseguiu dado novo.

        Sem isto o valor antigo continua na tela e parece que o botao nao faz
        nada — quando na verdade a API recusou a consulta.
        """
        if self.tray is not None:
            self.tray.show_message("Nao foi possivel atualizar", detail)

    def _toggle_bar(self) -> None:
        # Agendar no loop do tk mantem toda mexida de widget na thread certa.
        self.bar.root.after(0, self.bar.toggle)

    def _reset_position(self) -> None:
        self.bar.root.after(0, self.bar.reset_position)

    def _toggle_autostart(self) -> None:
        enabled = autostart.toggle()
        if self.tray is not None:
            self.tray.show_message(
                "Claude Code — barra de uso",
                "Inicio automatico ligado" if enabled else "Inicio automatico desligado",
            )

    def _open_config(self) -> None:
        if not self.config.path.exists():
            self.config.save()
        try:
            if sys.platform == "win32":
                os.startfile(str(self.config.path))  # noqa: S606
            else:
                subprocess.Popen(["xdg-open", str(self.config.path)])
        except Exception:
            pass

    # -------------------------------------------------------------- ciclo

    def run(self) -> int:
        self.monitor.start()
        if self.tray is not None:
            self.tray.start()
        self.bar.poll_snapshot(lambda: self.monitor.snapshot, interval_ms=1000)
        try:
            self.bar.run()
        except KeyboardInterrupt:
            pass
        finally:
            self.monitor.stop()
            if self.tray is not None:
                self.tray.stop()
        return 0

    def quit(self) -> None:
        self.monitor.stop()
        if self.tray is not None:
            self.tray.stop()
        self.bar.root.after(0, self.bar.quit)
