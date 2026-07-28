"""Tipos de dados compartilhados entre leitura, calculo e interface.

Este modulo nao importa nada de Windows nem de rede: e a fronteira entre as
tres camadas do projeto (sources -> monitor -> ui).
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Dict, Optional


class State(str, Enum):
    """Estado geral do snapshot mostrado na barra."""

    OK = "ok"
    """Percentual real disponivel."""

    UNKNOWN = "unknown"
    """Sem sessao ativa / sem dados. Barra fica neutra (cinza)."""

    ERROR = "error"
    """Falha ao ler os dados. Tambem neutra, mas com detalhe no tooltip."""


class Level(str, Enum):
    """Faixa de cor da barra."""

    NEUTRAL = "neutral"
    OK = "ok"
    WARN = "warn"
    DANGER = "danger"


# Nomes legiveis das janelas expostas pela API do Claude Code.
WINDOW_LABELS: Dict[str, str] = {
    "five_hour": "Sessao (5h)",
    "seven_day": "Semana (todos os modelos)",
    "seven_day_opus": "Semana (Opus)",
    "seven_day_sonnet": "Semana (Sonnet)",
}

# Duracao nominal de cada janela, em segundos. Valores confirmados no bundle do
# Claude Code (five_hour = 18000s, seven_day = 604800s).
WINDOW_SECONDS: Dict[str, int] = {
    "five_hour": 5 * 60 * 60,
    "seven_day": 7 * 24 * 60 * 60,
    "seven_day_opus": 7 * 24 * 60 * 60,
    "seven_day_sonnet": 7 * 24 * 60 * 60,
}


@dataclass(frozen=True)
class LimitWindow:
    """Uma janela de rate limit e o quanto dela ja foi consumido."""

    key: str
    percent: Optional[float]
    """0-100, ou None quando desconhecido."""

    resets_at: Optional[float] = None
    """Epoch em segundos (UTC) de quando a janela reinicia."""

    @property
    def label(self) -> str:
        return WINDOW_LABELS.get(self.key, self.key)

    def seconds_to_reset(self, now: Optional[float] = None) -> Optional[float]:
        if self.resets_at is None:
            return None
        now = time.time() if now is None else now
        return max(0.0, self.resets_at - now)

    def is_expired(self, now: Optional[float] = None) -> bool:
        """True quando a janela ja virou e o dado em maos esta velho."""
        remaining = self.seconds_to_reset(now)
        return remaining is not None and remaining <= 0


@dataclass(frozen=True)
class UsageSnapshot:
    """Foto do uso num instante. E o unico objeto que a UI consome."""

    state: State = State.UNKNOWN
    source: str = "none"
    """'api', 'transcripts' ou 'none'."""

    primary: Optional[LimitWindow] = None
    """Janela escolhida em config['metric'] — e a que a barra desenha."""

    windows: Dict[str, LimitWindow] = field(default_factory=dict)
    detail: str = ""
    """Mensagem curta pro tooltip (erro, aviso de calibragem, etc)."""

    captured_at: float = field(default_factory=time.time)
    tokens: Optional[int] = None
    """Tokens ponderados da janela atual (so no modo fallback)."""

    stale_seconds: Optional[float] = None
    """Ha quanto tempo o valor foi lido, quando ele vem do cache.

    Preenchido so quando o numero nao e de agora. A barra usa isso pra marcar
    o percentual com '~', porque quem bate o olho na barra nao ve o tooltip.
    """

    @property
    def is_stale(self) -> bool:
        return self.stale_seconds is not None and self.stale_seconds >= 90

    @property
    def percent(self) -> Optional[float]:
        return self.primary.percent if self.primary else None

    @property
    def has_percent(self) -> bool:
        return self.state is State.OK and self.percent is not None

    @classmethod
    def unknown(cls, detail: str = "", source: str = "none") -> "UsageSnapshot":
        return cls(state=State.UNKNOWN, source=source, detail=detail)

    @classmethod
    def error(cls, detail: str, source: str = "none") -> "UsageSnapshot":
        return cls(state=State.ERROR, source=source, detail=detail)


def level_for(percent: Optional[float], warn: float, danger: float) -> Level:
    """Mapeia percentual -> faixa de cor.

    Abaixo de `warn` verde, de `warn` (inclusive) ate `danger` amarelo, de
    `danger` (inclusive) pra cima vermelho.
    """
    if percent is None:
        return Level.NEUTRAL
    if percent >= danger:
        return Level.DANGER
    if percent >= warn:
        return Level.WARN
    return Level.OK


def format_duration(seconds: Optional[float]) -> str:
    """Formata segundos restantes de forma compacta: '4h12m', '38m', '<1m'."""
    if seconds is None:
        return "--"
    seconds = max(0.0, seconds)
    if seconds < 60:
        return "<1m"
    minutes = int(seconds // 60)
    if minutes < 60:
        return f"{minutes}m"
    hours, minutes = divmod(minutes, 60)
    if hours < 24:
        return f"{hours}h{minutes:02d}m"
    days, hours = divmod(hours, 24)
    return f"{days}d{hours:02d}h"
