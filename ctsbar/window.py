"""Calculo da janela rolante de 5h a partir de eventos locais.

Usado so pelo fallback (quando a API nao responde). A regra e a mesma que
ferramentas como o ccusage adotam, e bate com o comportamento observavel do
Claude Code:

* a janela abre no primeiro evento depois de um periodo ocioso;
* o inicio e arredondado pra baixo na hora cheia;
* a janela dura 5h (18000s, valor confirmado no bundle da CLI);
* um intervalo maior que a janela sem nenhum evento tambem abre uma nova.

Nao ha como derivar isso com exatidao offline — a janela real e do lado do
servidor. Por isso o resultado daqui e sempre tratado como estimativa.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterable, List, Optional

WINDOW_SECONDS = 5 * 60 * 60
HOUR_SECONDS = 60 * 60


@dataclass
class UsageEvent:
    """Uma resposta do assistente, com os tokens que ela custou."""

    timestamp: float
    input_tokens: int = 0
    output_tokens: int = 0
    cache_creation_tokens: int = 0
    cache_read_tokens: int = 0

    @property
    def raw_tokens(self) -> int:
        return (
            self.input_tokens
            + self.output_tokens
            + self.cache_creation_tokens
            + self.cache_read_tokens
        )

    def weighted_tokens(self, weights: dict) -> float:
        """Soma ponderada: cache lido e barato, output e caro."""
        return (
            self.input_tokens * float(weights.get("input", 1.0))
            + self.output_tokens * float(weights.get("output", 5.0))
            + self.cache_creation_tokens * float(weights.get("cache_creation", 1.25))
            + self.cache_read_tokens * float(weights.get("cache_read", 0.1))
        )


@dataclass
class SessionBlock:
    """Uma janela de 5h com os eventos que cairam dentro dela."""

    start: float
    end: float
    events: List[UsageEvent] = field(default_factory=list)

    @property
    def raw_tokens(self) -> int:
        return sum(event.raw_tokens for event in self.events)

    def weighted_tokens(self, weights: dict) -> float:
        return sum(event.weighted_tokens(weights) for event in self.events)

    @property
    def last_activity(self) -> Optional[float]:
        return max((event.timestamp for event in self.events), default=None)

    def is_active(self, now: float) -> bool:
        return self.start <= now < self.end


def floor_to_hour(timestamp: float) -> float:
    return timestamp - (timestamp % HOUR_SECONDS)


def build_blocks(
    events: Iterable[UsageEvent], window_seconds: int = WINDOW_SECONDS
) -> List[SessionBlock]:
    """Agrupa eventos em janelas de 5h consecutivas."""
    ordered = sorted(events, key=lambda e: e.timestamp)
    blocks: List[SessionBlock] = []

    for event in ordered:
        current = blocks[-1] if blocks else None
        gap_too_big = (
            current is not None
            and current.events
            and event.timestamp - current.events[-1].timestamp >= window_seconds
        )
        if current is None or event.timestamp >= current.end or gap_too_big:
            start = floor_to_hour(event.timestamp)
            blocks.append(SessionBlock(start=start, end=start + window_seconds))
            current = blocks[-1]
        current.events.append(event)

    return blocks


def current_block(
    events: Iterable[UsageEvent],
    now: Optional[float] = None,
    window_seconds: int = WINDOW_SECONDS,
) -> Optional[SessionBlock]:
    """Devolve a janela que contem `now`, ou None se ela ja fechou.

    Retornar None e o que faz a barra zerar sozinha quando a janela reseta.
    """
    import time

    now = time.time() if now is None else now
    blocks = build_blocks(events, window_seconds)
    if not blocks:
        return None
    last = blocks[-1]
    return last if last.is_active(now) else None
