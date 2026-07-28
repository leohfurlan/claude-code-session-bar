"""Icone na bandeja do sistema: o mesmo percentual, em miniatura, mais o menu.

Depende de pystray + Pillow. Se as duas nao estiverem instaladas, o app roda
so com a barra flutuante (veja app.py).
"""

from __future__ import annotations

import threading
from typing import Callable, Optional

from ..config import Config
from ..models import State, UsageSnapshot, format_duration, level_for

try:  # pragma: no cover - depende do ambiente
    import pystray
    from PIL import Image, ImageDraw

    TRAY_AVAILABLE = True
except ImportError:  # pragma: no cover
    pystray = None
    Image = ImageDraw = None
    TRAY_AVAILABLE = False

ICON_SIZE = 64


class TrayIcon:
    """Icone da bandeja que espelha o estado da barra."""

    def __init__(
        self,
        config: Config,
        get_snapshot: Callable[[], UsageSnapshot],
        on_refresh: Callable[[], None],
        on_toggle_bar: Callable[[], None],
        on_reset_position: Callable[[], None],
        on_toggle_autostart: Callable[[], None],
        on_open_config: Callable[[], None],
        on_quit: Callable[[], None],
        is_autostart_enabled: Callable[[], bool],
        is_bar_visible: Callable[[], bool],
    ):
        self.config = config
        self._get_snapshot = get_snapshot
        self._on_refresh = on_refresh
        self._on_toggle_bar = on_toggle_bar
        self._on_reset_position = on_reset_position
        self._on_toggle_autostart = on_toggle_autostart
        self._on_open_config = on_open_config
        self._on_quit = on_quit
        self._is_autostart_enabled = is_autostart_enabled
        self._is_bar_visible = is_bar_visible

        self._icon = None
        self._thread: Optional[threading.Thread] = None
        self._last_key = None

    # ------------------------------------------------------------ visual

    def _color(self, snapshot: UsageSnapshot) -> str:
        colors = self.config.get("colors", {})
        if not snapshot.has_percent:
            return colors.get("neutral", "#5a5a5a")
        level = level_for(snapshot.percent, self.config.warn, self.config.danger)
        return colors.get(level.value, colors.get("neutral", "#5a5a5a"))

    def _render_icon(self, snapshot: UsageSnapshot):
        """Barra vertical: cheia de baixo pra cima conforme o uso sobe."""
        colors = self.config.get("colors", {})
        image = Image.new("RGBA", (ICON_SIZE, ICON_SIZE), (0, 0, 0, 0))
        draw = ImageDraw.Draw(image)

        draw.rounded_rectangle(
            [4, 4, ICON_SIZE - 4, ICON_SIZE - 4],
            radius=10,
            fill=colors.get("background", "#1e1e1e"),
            outline=colors.get("border", "#3c3c3c"),
            width=2,
        )

        percent = snapshot.percent if snapshot.has_percent else 0.0
        inner_top, inner_bottom = 10, ICON_SIZE - 10
        height = inner_bottom - inner_top
        filled = int(height * max(0.0, min(100.0, percent)) / 100.0)
        if filled > 0:
            draw.rounded_rectangle(
                [10, inner_bottom - filled, ICON_SIZE - 10, inner_bottom],
                radius=4,
                fill=self._color(snapshot),
            )
        elif not snapshot.has_percent:
            draw.line(
                [12, ICON_SIZE // 2, ICON_SIZE - 12, ICON_SIZE // 2],
                fill=colors.get("neutral", "#5a5a5a"),
                width=4,
            )
        return image

    def _tooltip(self, snapshot: UsageSnapshot) -> str:
        lines = ["Claude Code — uso da sessao"]

        if snapshot.has_percent and snapshot.primary is not None:
            remaining = snapshot.primary.seconds_to_reset()
            line = f"{int(snapshot.percent)}% de {snapshot.primary.label}"
            if remaining is not None:
                line += f" · reseta em {format_duration(remaining)}"
            lines.append(line)
        elif snapshot.state is State.ERROR:
            lines.append("Sem leitura de uso")
        elif snapshot.tokens:
            lines.append(f"~{snapshot.tokens // 1000}k tokens ponderados na janela de 5h")
        else:
            lines.append("Sem sessao ativa")

        for key, window in snapshot.windows.items():
            if snapshot.primary is not None and key == snapshot.primary.key:
                continue
            if window.percent is None:
                continue
            lines.append(f"{window.label}: {int(window.percent)}%")

        if snapshot.detail:
            lines.append(snapshot.detail)

        # O tooltip do Windows corta perto de 128 caracteres.
        return "\n".join(lines)[:127]

    # -------------------------------------------------------------- menu

    # Os callables de menu do pystray sao chamados com aridade variavel
    # dependendo da versao (texto recebe o item, acao recebe icone + item).
    # `*_` aceita qualquer uma sem depender de introspeccao.

    def _header_text(self, *_) -> str:
        snapshot = self._get_snapshot()
        if snapshot.has_percent and snapshot.primary is not None:
            remaining = snapshot.primary.seconds_to_reset()
            suffix = f" · {format_duration(remaining)}" if remaining is not None else ""
            return f"{int(snapshot.percent)}% — {snapshot.primary.label}{suffix}"
        if snapshot.tokens:
            return f"~{snapshot.tokens // 1000}k tokens na janela de 5h"
        return "Sem sessao ativa"

    def _source_text(self, *_) -> str:
        source = self._get_snapshot().source
        return {
            "api": "Fonte: API do Claude Code (exato)",
            "transcripts": "Fonte: transcripts locais (estimativa)",
        }.get(source, "Fonte: nenhuma")

    def _build_menu(self):
        return pystray.Menu(
            pystray.MenuItem(self._header_text, None, enabled=False),
            pystray.MenuItem(self._source_text, None, enabled=False),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("Atualizar agora", lambda *_: self._on_refresh()),
            pystray.MenuItem(
                "Mostrar barra",
                lambda *_: self._on_toggle_bar(),
                checked=lambda *_: self._is_bar_visible(),
            ),
            pystray.MenuItem("Reposicionar barra", lambda *_: self._on_reset_position()),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem(
                "Iniciar com o Windows",
                lambda *_: self._on_toggle_autostart(),
                checked=lambda *_: self._is_autostart_enabled(),
            ),
            pystray.MenuItem("Abrir config.json", lambda *_: self._on_open_config()),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("Sair", lambda *_: self._on_quit()),
        )

    # -------------------------------------------------------------- ciclo

    def start(self) -> bool:
        if not TRAY_AVAILABLE:
            return False
        snapshot = self._get_snapshot()
        self._icon = pystray.Icon(
            "ctsbar",
            icon=self._render_icon(snapshot),
            title=self._tooltip(snapshot),
            menu=self._build_menu(),
        )
        # O backend win32 do pystray monta o proprio message loop na thread em
        # que roda, entao pode viver fora da principal (que e do tkinter).
        self._thread = threading.Thread(target=self._run_safe, name="ctsbar-tray", daemon=True)
        self._thread.start()
        return True

    def _run_safe(self) -> None:
        """Roda o icone isolando falhas: a barra nunca cai junto com a bandeja.

        Sem isto, um erro aqui some sem deixar rastro quando o app roda por
        pythonw, que nao tem console pra onde imprimir o traceback.
        """
        try:
            self._icon.run()
        except Exception:
            import traceback
            from datetime import datetime

            from ..paths import app_config_dir

            try:
                app_config_dir().mkdir(parents=True, exist_ok=True)
                with (app_config_dir() / "error.log").open("a", encoding="utf-8") as handle:
                    handle.write(f"\n{'=' * 70}\n{datetime.now():%Y-%m-%d %H:%M:%S}  [bandeja]\n")
                    traceback.print_exc(file=handle)
            except Exception:
                pass

    def update(self, snapshot: UsageSnapshot) -> None:
        """Redesenha o icone so quando o percentual visivel muda."""
        if self._icon is None:
            return
        key = (
            snapshot.state,
            None if snapshot.percent is None else round(snapshot.percent),
            snapshot.detail,
        )
        if key == self._last_key:
            return
        self._last_key = key
        try:
            self._icon.icon = self._render_icon(snapshot)
            self._icon.title = self._tooltip(snapshot)
        except Exception:
            pass

    def show_message(self, title: str, message: str) -> None:
        if self._icon is None:
            return
        try:
            self._icon.notify(message, title)
        except Exception:
            pass

    def stop(self) -> None:
        if self._icon is not None:
            try:
                self._icon.stop()
            except Exception:
                pass
