"""A barrinha em si: janela tkinter sem borda, sempre no topo.

Por padrao ela fica flutuando encostada na borda de cima da taskbar, do lado
da area de notificacao. Com `taskbar_embed: true` na config, tenta entrar
dentro da taskbar de verdade (veja ui/taskbar_embed.py) e volta pro modo
flutuante se nao conseguir.

Interacao:
  * arrastar com o botao esquerdo move e grava a posicao;
  * botao direito forca uma atualizacao imediata;
  * duplo clique esconde a barra (o icone da bandeja traz de volta).
"""

from __future__ import annotations

from typing import Callable, Optional

try:
    import tkinter as tk
except ImportError as exc:  # pragma: no cover - depende da instalacao
    raise SystemExit(
        "tkinter nao encontrado. Ele vem junto com o Python oficial do "
        "python.org no Windows — reinstale marcando 'tcl/tk and IDLE', ou "
        "use `python -m ctsbar --watch` pro modo texto."
    ) from exc

from ..config import Config
from ..models import Level, State, UsageSnapshot, format_duration, level_for
from ..winapi import (
    IS_WINDOWS,
    enable_dpi_awareness,
    is_topmost,
    make_tool_window,
    set_topmost,
    work_area,
)
from .taskbar_embed import try_embed, undo_embed


class UsageBar:
    """Desenha o snapshot como barra horizontal colorida."""

    def __init__(self, config: Config, on_refresh: Optional[Callable[[], None]] = None):
        self.config = config
        self._on_refresh = on_refresh
        self._embedded = False
        self._drag_origin: Optional[tuple] = None
        self._last_key: Optional[tuple] = None

        enable_dpi_awareness()

        self.root = tk.Tk()
        self.root.withdraw()
        self.root.title("Claude Code — uso da sessao")
        self.root.overrideredirect(True)
        self.root.attributes("-topmost", True)
        try:
            self.root.attributes("-alpha", float(config.get("bar.opacity", 0.92)))
        except tk.TclError:
            pass

        self.width = int(config.get("bar.width", 190))
        self.height = int(config.get("bar.height", 18))
        colors = config.get("colors", {})

        self.canvas = tk.Canvas(
            self.root,
            width=self.width,
            height=self.height,
            highlightthickness=0,
            bd=0,
            bg=colors.get("background", "#1e1e1e"),
            cursor="hand2",
        )
        self.canvas.pack(fill="both", expand=True)

        self._bind_events()
        self._place_window()

        if config.get("bar.visible", True):
            self.show()

        self.render(UsageSnapshot.unknown("Carregando…"))

    # ------------------------------------------------------------ janela

    def _bind_events(self) -> None:
        self.canvas.bind("<Button-1>", self._on_drag_start)
        self.canvas.bind("<B1-Motion>", self._on_drag)
        self.canvas.bind("<ButtonRelease-1>", self._on_drag_end)
        self.canvas.bind("<Button-3>", lambda _e: self._on_refresh() if self._on_refresh else None)
        self.canvas.bind("<Double-Button-1>", lambda _e: self.hide())

    def _default_position(self) -> tuple:
        """Canto direito, encostado no topo da taskbar."""
        margin_x = int(self.config.get("bar.margin_x", 12))
        margin_y = int(self.config.get("bar.margin_y", 2))

        area = work_area()
        if area is not None:
            _left, _top, right, bottom = area
            return right - self.width - margin_x, bottom - self.height - margin_y

        # Fora do Windows (ou se a chamada falhar): canto inferior direito.
        screen_w = self.root.winfo_screenwidth()
        screen_h = self.root.winfo_screenheight()
        return screen_w - self.width - margin_x, screen_h - self.height - 48

    def _place_window(self) -> None:
        x = self.config.get("bar.x")
        y = self.config.get("bar.y")
        if not isinstance(x, int) or not isinstance(y, int):
            x, y = self._default_position()
        self.root.geometry(f"{self.width}x{self.height}+{int(x)}+{int(y)}")

    def _apply_window_styles(self) -> None:
        """Estilos que so existem depois que a janela tem HWND."""
        if not IS_WINDOWS:
            return
        try:
            hwnd = int(self.root.winfo_id())
        except Exception:
            return

        make_tool_window(hwnd)

        if self.config.get("taskbar_embed", False) and not self._embedded:
            self._embedded = try_embed(hwnd, self.width, self.height)
            if not self._embedded:
                # Nao deu: segue flutuante, e o modo confiavel.
                self._place_window()

    def show(self) -> None:
        # Ordem importa: mexer no ex-style de uma janela ja mapeada derruba ela
        # da faixa topmost. Estiliza primeiro, mapeia depois, topmost por ultimo.
        self.root.update_idletasks()  # garante que o HWND ja exista
        self._apply_window_styles()
        self.root.deiconify()
        self.root.attributes("-topmost", True)
        self._reassert_topmost()
        self.config.set("bar.visible", True)

    def _reassert_topmost(self) -> None:
        """Recoloca a barra no topo se algum app a tiver empurrado pra tras.

        Janela `overrideredirect` no Windows perde o topmost quando outro
        processo assume o foreground — jogo em tela cheia, UAC, troca de
        aplicativo. So o atributo do tk nao segura; e preciso reafirmar.
        """
        if self._embedded or not IS_WINDOWS or not self.visible:
            return
        try:
            hwnd = int(self.root.winfo_id())
        except Exception:
            return
        if not is_topmost(hwnd):
            try:
                self.root.attributes("-topmost", True)
            except tk.TclError:
                pass
        set_topmost(hwnd)

    def hide(self) -> None:
        if self._embedded:
            try:
                undo_embed(int(self.root.winfo_id()))
            except Exception:
                pass
            self._embedded = False
        self.root.withdraw()
        self.config.set("bar.visible", False)
        self.config.save()

    def toggle(self) -> None:
        if self.root.state() == "withdrawn":
            self.show()
        else:
            self.hide()

    @property
    def visible(self) -> bool:
        return self.root.state() != "withdrawn"

    # ------------------------------------------------------------- drag

    def _on_drag_start(self, event) -> None:
        if self._embedded:
            return
        self._drag_origin = (event.x_root - self.root.winfo_x(), event.y_root - self.root.winfo_y())

    def _on_drag(self, event) -> None:
        if self._drag_origin is None:
            return
        offset_x, offset_y = self._drag_origin
        self.root.geometry(f"+{event.x_root - offset_x}+{event.y_root - offset_y}")

    def _on_drag_end(self, _event) -> None:
        if self._drag_origin is None:
            return
        self._drag_origin = None
        self.config.set("bar.x", self.root.winfo_x())
        self.config.set("bar.y", self.root.winfo_y())
        self.config.save()

    def reset_position(self) -> None:
        self.config.set("bar.x", None)
        self.config.set("bar.y", None)
        self.config.save()
        self._place_window()

    # ---------------------------------------------------------- desenho

    def _color_for(self, snapshot: UsageSnapshot) -> str:
        colors = self.config.get("colors", {})
        if not snapshot.has_percent:
            return colors.get("neutral", "#5a5a5a")
        level = level_for(snapshot.percent, self.config.warn, self.config.danger)
        return colors.get(level.value, colors.get("neutral", "#5a5a5a"))

    def _label_for(self, snapshot: UsageSnapshot) -> str:
        if not snapshot.has_percent:
            if snapshot.state is State.ERROR:
                return "erro"
            # Sem percentual mas com atividade medida: mostra o volume bruto,
            # que ja diz alguma coisa, em vez de fingir que nao ha sessao.
            if snapshot.tokens:
                return f"~{snapshot.tokens // 1000}k tok"
            return "sem sessao"
        text = f"{int(snapshot.percent)}%"
        remaining = snapshot.primary.seconds_to_reset() if snapshot.primary else None
        if remaining is not None:
            text += f"  {format_duration(remaining)}"
        return text

    def render(self, snapshot: UsageSnapshot) -> None:
        """Redesenha. Sai cedo se nada visivel mudou (economiza CPU)."""
        percent = snapshot.percent if snapshot.has_percent else None
        remaining = snapshot.primary.seconds_to_reset() if snapshot.primary else None
        key = (
            snapshot.state,
            None if percent is None else round(percent),
            None if remaining is None else int(remaining // 60),
            snapshot.detail,
        )
        if key == self._last_key:
            return
        self._last_key = key

        colors = self.config.get("colors", {})
        fill = self._color_for(snapshot)

        self.canvas.delete("all")
        self.canvas.create_rectangle(
            0, 0, self.width, self.height,
            fill=colors.get("background", "#1e1e1e"), outline="",
        )

        if percent is not None and percent > 0:
            filled = max(1, int(self.width * min(100.0, percent) / 100.0))
            self.canvas.create_rectangle(0, 0, filled, self.height, fill=fill, outline="")
        elif percent is None:
            # Estado neutro: uma faixa fina, so pra barra nao sumir da tela.
            self.canvas.create_rectangle(
                0, self.height - 2, self.width, self.height, fill=fill, outline=""
            )

        self.canvas.create_rectangle(
            0, 0, self.width - 1, self.height - 1,
            outline=colors.get("border", "#3c3c3c"),
        )

        if self.config.get("bar.show_text", True):
            self.canvas.create_text(
                self.width // 2,
                self.height // 2,
                text=self._label_for(snapshot),
                fill=colors.get("text", "#f0f0f0"),
                font=("Segoe UI", int(self.config.get("bar.font_size", 8)), "bold"),
            )

    # ------------------------------------------------------------- loop

    def poll_snapshot(self, getter: Callable[[], UsageSnapshot], interval_ms: int = 1000) -> None:
        """Le o snapshot do monitor no loop do tk.

        O monitor roda em outra thread; widgets tk so podem ser tocados na
        thread principal. Por isso a UI puxa o estado em vez de receber push.
        """

        # A cada ~3s conferimos o topmost. E uma leitura de flag mais, no pior
        # caso, um SetWindowPos: barato o bastante pra rodar pra sempre.
        ticks_per_check = max(1, 3000 // max(1, interval_ms))
        counter = {"n": 0}

        def tick() -> None:
            try:
                self.render(getter())
                counter["n"] += 1
                if counter["n"] % ticks_per_check == 0:
                    self._reassert_topmost()
            except Exception:
                pass
            self.root.after(interval_ms, tick)

        self.root.after(0, tick)

    def run(self) -> None:
        self.root.mainloop()

    def quit(self) -> None:
        try:
            self.root.quit()
            self.root.destroy()
        except Exception:
            pass
