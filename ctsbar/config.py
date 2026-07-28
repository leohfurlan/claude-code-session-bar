"""Configuracao em JSON, com defaults sensatos e merge tolerante a erro."""

from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any, Dict, Optional

from .paths import app_config_dir, app_config_path

DEFAULTS: Dict[str, Any] = {
    # Intervalo de atualizacao. O enunciado pede 15-30s; 20s e o meio termo.
    "poll_seconds": 20,
    # Qual janela a barra desenha. Alem das chaves da API, aceita "worst",
    # que escolhe a janela com maior percentual.
    "metric": "five_hour",
    "thresholds": {"warn": 50.0, "danger": 80.0},
    "bar": {
        "width": 190,
        "height": 18,
        # Distancia da borda direita / do topo da taskbar quando a posicao
        # nao foi fixada manualmente pelo usuario (arrastando a barra).
        "margin_x": 12,
        "margin_y": 2,
        "x": None,
        "y": None,
        "opacity": 0.92,
        "show_text": True,
        "visible": True,
        "font_size": 8,
        # Tira a barra do Alt+Tab e impede que ela roube foco. Se a janela nao
        # aparecer, desligue isto: e o unico ajuste que mexe em estilo nativo.
        "tool_window": True,
    },
    # Reparenting de verdade dentro do Shell_TrayWnd. Experimental: se falhar,
    # a barra volta sozinha pro modo flutuante.
    "taskbar_embed": False,
    "api": {
        "enabled": True,
        "timeout_seconds": 6,
        "base_url": "https://api.anthropic.com",
        # A API devolve utilization em 0-100. "fraction" multiplica por 100,
        # caso alguma versao futura mude a escala.
        "utilization_scale": "percent",
    },
    "fallback": {
        "enabled": True,
        # Sem orcamento configurado a barra mostra tokens + tempo, sem inventar
        # um percentual. Veja "Calibrar o fallback" no README.
        "token_budget": None,
        "weights": {
            "input": 1.0,
            "output": 5.0,
            "cache_creation": 1.25,
            "cache_read": 0.1,
        },
    },
    "colors": {
        "ok": "#2ecc71",
        "warn": "#f1c40f",
        "danger": "#e74c3c",
        "neutral": "#5a5a5a",
        "background": "#1e1e1e",
        "text": "#f0f0f0",
        "border": "#3c3c3c",
    },
}


def _deep_merge(base: Dict[str, Any], override: Dict[str, Any]) -> Dict[str, Any]:
    out = copy.deepcopy(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = _deep_merge(out[key], value)
        else:
            out[key] = value
    return out


class Config:
    """Wrapper fino sobre o dict, com acesso por caminho ('bar.width')."""

    def __init__(self, data: Optional[Dict[str, Any]] = None, path: Optional[Path] = None):
        self.data = _deep_merge(DEFAULTS, data or {})
        self.path = path or app_config_path()

    @classmethod
    def load(cls, path: Optional[Path] = None) -> "Config":
        path = path or app_config_path()
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(raw, dict):
                raw = {}
        except (OSError, ValueError):
            raw = {}
        return cls(raw, path)

    def save(self) -> bool:
        try:
            app_config_dir().mkdir(parents=True, exist_ok=True)
            self.path.write_text(
                json.dumps(self.data, indent=2, ensure_ascii=False), encoding="utf-8"
            )
            return True
        except OSError:
            return False

    def get(self, dotted: str, default: Any = None) -> Any:
        node: Any = self.data
        for part in dotted.split("."):
            if not isinstance(node, dict) or part not in node:
                return default
            node = node[part]
        return node

    def set(self, dotted: str, value: Any) -> None:
        parts = dotted.split(".")
        node = self.data
        for part in parts[:-1]:
            nxt = node.get(part)
            if not isinstance(nxt, dict):
                nxt = {}
                node[part] = nxt
            node = nxt
        node[parts[-1]] = value

    @property
    def poll_seconds(self) -> float:
        try:
            value = float(self.get("poll_seconds", 20))
        except (TypeError, ValueError):
            value = 20.0
        # Piso de 5s pra nao martelar a API por engano.
        return max(5.0, value)

    @property
    def warn(self) -> float:
        return float(self.get("thresholds.warn", 50.0))

    @property
    def danger(self) -> float:
        return float(self.get("thresholds.danger", 80.0))
