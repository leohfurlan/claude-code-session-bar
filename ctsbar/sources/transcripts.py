"""Fonte de fallback: parsing dos transcripts JSONL do Claude Code.

Cada sessao vira um arquivo em
`%USERPROFILE%\\.claude\\projects\\<cwd-com-hifens>\\<session-uuid>.jsonl`,
com uma entrada JSON por linha. As que interessam sao as do assistente:

    {"type": "assistant",
     "timestamp": "2026-07-28T13:32:41.884Z",
     "requestId": "req_...",
     "message": {"id": "msg_...", "usage": {
         "input_tokens": 2, "output_tokens": 304,
         "cache_creation_input_tokens": 38011, "cache_read_input_tokens": 0}}}

A leitura e incremental: guardamos o offset ja lido de cada arquivo e, no poll
seguinte, so processamos o que foi acrescentado. Isso mantem o consumo de CPU
proximo de zero mesmo com historico grande.

Importante: isto e uma *estimativa*. O percentual real e do lado do servidor
(veja sources/api.py). Sem `fallback.token_budget` configurado, este modulo
devolve tokens e tempo de janela, sem inventar um percentual.
"""

from __future__ import annotations

import json
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

from ..models import UsageSnapshot, LimitWindow, State, format_duration
from ..paths import iter_transcripts, state_path
from ..window import WINDOW_SECONDS, UsageEvent, current_block

# Olhamos so os arquivos tocados nas ultimas ~6h: mais que a janela de 5h, com
# folga pro relogio e pra escrita atrasada.
LOOKBACK_SECONDS = 6 * 60 * 60


def parse_timestamp(value: Any) -> Optional[float]:
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.timestamp()


def parse_line(line: str) -> Optional[Tuple[str, UsageEvent]]:
    """Extrai (chave-de-dedupe, evento) de uma linha do transcript.

    Devolve None pra qualquer linha que nao seja resposta do assistente com
    bloco de usage — inclusive linhas truncadas, que acontecem quando lemos o
    arquivo enquanto a CLI ainda esta escrevendo nele.
    """
    line = line.strip()
    if not line or '"assistant"' not in line:
        return None
    try:
        entry = json.loads(line)
    except ValueError:
        return None
    if not isinstance(entry, dict) or entry.get("type") != "assistant":
        return None

    message = entry.get("message")
    if not isinstance(message, dict):
        return None
    usage = message.get("usage")
    if not isinstance(usage, dict):
        return None

    timestamp = parse_timestamp(entry.get("timestamp"))
    if timestamp is None:
        return None

    def count(key: str) -> int:
        value = usage.get(key)
        return int(value) if isinstance(value, (int, float)) and value > 0 else 0

    event = UsageEvent(
        timestamp=timestamp,
        input_tokens=count("input_tokens"),
        output_tokens=count("output_tokens"),
        cache_creation_tokens=count("cache_creation_input_tokens"),
        cache_read_tokens=count("cache_read_input_tokens"),
    )

    # Uma mesma resposta pode aparecer em mais de um arquivo (resume, sidechain,
    # compactacao). message.id + requestId identifica a chamada de forma unica.
    key = f"{message.get('id') or ''}|{entry.get('requestId') or ''}"
    if key == "|":
        key = f"{timestamp}|{event.raw_tokens}"
    return key, event


class TranscriptUsageSource:
    """Estima o uso da janela atual lendo os transcripts locais."""

    name = "transcripts"

    def __init__(self, config):
        self.config = config
        # path -> (offset ja lido, tamanho visto na ultima leitura)
        self._offsets: Dict[str, Tuple[int, int]] = {}
        self._events: Dict[str, UsageEvent] = {}
        self._load_state()

    @property
    def enabled(self) -> bool:
        return bool(self.config.get("fallback.enabled", True))

    # ---------------------------------------------------------------- estado

    def _load_state(self) -> None:
        """Recupera offsets *e* eventos ja lidos.

        Os dois andam juntos: guardar so os offsets faria um processo recem
        iniciado pular tudo que ja foi lido e contar a janela pela metade.
        """
        try:
            raw = json.loads(state_path().read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return
        if not isinstance(raw, dict):
            return

        offsets = raw.get("offsets")
        if isinstance(offsets, dict):
            for path, value in offsets.items():
                if isinstance(value, list) and len(value) == 2:
                    try:
                        self._offsets[path] = (int(value[0]), int(value[1]))
                    except (TypeError, ValueError):
                        continue

        cutoff = time.time() - LOOKBACK_SECONDS
        events = raw.get("events")
        if isinstance(events, dict):
            for key, value in events.items():
                if not isinstance(value, list) or len(value) != 5:
                    continue
                try:
                    event = UsageEvent(
                        timestamp=float(value[0]),
                        input_tokens=int(value[1]),
                        output_tokens=int(value[2]),
                        cache_creation_tokens=int(value[3]),
                        cache_read_tokens=int(value[4]),
                    )
                except (TypeError, ValueError):
                    continue
                if event.timestamp >= cutoff:
                    self._events[key] = event

        # Estado inconsistente (eventos perdidos mas offsets no fim do arquivo)
        # faria a janela ser subestimada: melhor reler tudo do zero.
        if self._offsets and not self._events:
            self._offsets.clear()

    def _save_state(self) -> None:
        try:
            state_path().parent.mkdir(parents=True, exist_ok=True)
            payload = {
                "offsets": {k: list(v) for k, v in self._offsets.items()},
                "events": {
                    key: [
                        event.timestamp,
                        event.input_tokens,
                        event.output_tokens,
                        event.cache_creation_tokens,
                        event.cache_read_tokens,
                    ]
                    for key, event in self._events.items()
                },
            }
            state_path().write_text(json.dumps(payload), encoding="utf-8")
        except OSError:
            pass

    # --------------------------------------------------------------- leitura

    def _read_new_lines(self, path: Path) -> List[str]:
        """Le so o que foi acrescentado desde a ultima passada."""
        key = str(path)
        try:
            size = path.stat().st_size
        except OSError:
            return []

        offset, previous_size = self._offsets.get(key, (0, 0))
        # Arquivo encolheu -> foi rotacionado/reescrito: le do zero.
        if size < previous_size:
            offset = 0
        if size == previous_size and offset >= size:
            return []

        try:
            with path.open("r", encoding="utf-8", errors="replace") as handle:
                handle.seek(offset)
                data = handle.read()
                new_offset = handle.tell()
        except OSError:
            return []

        # Se a ultima linha veio pela metade (a CLI ainda estava escrevendo),
        # devolvemos o offset pro inicio dela e relemos completa no proximo poll.
        if data and not data.endswith("\n"):
            cut = data.rfind("\n")
            if cut == -1:
                return []
            new_offset -= len(data) - (cut + 1)
            data = data[: cut + 1]

        self._offsets[key] = (new_offset, size)
        return data.splitlines()

    def _collect(self) -> None:
        seen: Set[str] = set()
        for path in iter_transcripts(max_age_seconds=LOOKBACK_SECONDS):
            seen.add(str(path))
            for line in self._read_new_lines(path):
                parsed = parse_line(line)
                if parsed is not None:
                    key, event = parsed
                    self._events[key] = event

        # Poda: eventos e offsets fora da janela de interesse nao servem mais.
        cutoff = time.time() - LOOKBACK_SECONDS
        self._events = {k: e for k, e in self._events.items() if e.timestamp >= cutoff}
        self._offsets = {k: v for k, v in self._offsets.items() if k in seen}

    # -------------------------------------------------------------- snapshot

    def read(self) -> UsageSnapshot:
        """Nunca levanta excecao."""
        if not self.enabled:
            return UsageSnapshot.unknown("Fallback desativado", source=self.name)

        try:
            self._collect()
            self._save_state()
        except Exception as exc:  # defensivo: a barra nunca pode cair
            return UsageSnapshot.error(f"Falha lendo transcripts: {exc}", source=self.name)

        now = time.time()
        block = current_block(self._events.values(), now=now)
        if block is None:
            # Nenhuma atividade na janela: e exatamente o estado "zerado".
            return UsageSnapshot(
                state=State.OK,
                source=self.name,
                primary=LimitWindow(key="five_hour", percent=0.0, resets_at=None),
                windows={},
                detail="Estimado dos transcripts locais — sem atividade na janela atual",
                tokens=0,
            )

        weights = self.config.get("fallback.weights", {}) or {}
        weighted = block.weighted_tokens(weights)
        budget = self.config.get("fallback.token_budget")

        window = LimitWindow(
            key="five_hour",
            percent=None,
            resets_at=block.start + WINDOW_SECONDS,
        )
        detail = (
            f"Estimado dos transcripts locais — {block.raw_tokens:,} tokens brutos "
            f"({int(weighted):,} ponderados) na janela de 5h"
        ).replace(",", ".")

        if isinstance(budget, (int, float)) and budget > 0:
            window = LimitWindow(
                key=window.key,
                percent=max(0.0, min(100.0, weighted / float(budget) * 100.0)),
                resets_at=window.resets_at,
            )
        else:
            detail += " · defina fallback.token_budget pra ver percentual"

        remaining = window.seconds_to_reset(now)
        if remaining is not None:
            detail += f" · reseta em {format_duration(remaining)}"

        return UsageSnapshot(
            state=State.OK if window.percent is not None else State.UNKNOWN,
            source=self.name,
            primary=window,
            windows={window.key: window},
            detail=detail,
            tokens=int(weighted),
        )
