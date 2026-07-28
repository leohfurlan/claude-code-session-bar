"""Thread de polling: decide qual fonte usar e publica o snapshot atual.

Regra: tenta a API (percentual exato); se ela nao entregar, cai pros
transcripts locais (estimativa). Qualquer falha das duas vira estado neutro —
a barra nunca fica sem resposta e o processo nunca morre.
"""

from __future__ import annotations

import threading
import time
from typing import Callable, List, Optional

from .config import Config
from .models import State, UsageSnapshot
from .sources import ApiUsageSource, TranscriptUsageSource


class UsageMonitor:
    """Roda em background e chama `on_update` a cada snapshot novo."""

    def __init__(self, config: Config, on_update: Optional[Callable[[UsageSnapshot], None]] = None):
        self.config = config
        self._on_update = on_update
        self._api = ApiUsageSource(config)
        self._transcripts = TranscriptUsageSource(config)
        self._snapshot = UsageSnapshot.unknown("Iniciando…")
        self._lock = threading.Lock()
        self._wake = threading.Event()
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None

    # ------------------------------------------------------------- ciclo

    @property
    def snapshot(self) -> UsageSnapshot:
        with self._lock:
            return self._snapshot

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, name="ctsbar-monitor", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        self._wake.set()

    def refresh_now(self) -> None:
        """Forca um ciclo imediato (usado pelo menu 'Atualizar agora')."""
        self._wake.set()

    def _loop(self) -> None:
        while not self._stop.is_set():
            try:
                self._publish(self.poll())
            except Exception as exc:  # rede/disco/qualquer coisa
                self._publish(UsageSnapshot.error(f"Erro inesperado: {exc}"))

            # Event.wait dorme de verdade: sem busy loop, CPU ~0% ocioso.
            self._wake.wait(self.config.poll_seconds)
            self._wake.clear()

    def _publish(self, snapshot: UsageSnapshot) -> None:
        with self._lock:
            self._snapshot = snapshot
        if self._on_update is not None:
            try:
                self._on_update(snapshot)
            except Exception:
                pass

    # ------------------------------------------------------------- fontes

    def poll(self) -> UsageSnapshot:
        """Um ciclo de leitura. Nunca levanta excecao."""
        notes: List[str] = []

        if self._api.enabled:
            snapshot = self._api.read()
            if snapshot.state is State.OK and snapshot.primary is not None:
                return self._expire_if_stale(snapshot)
            if snapshot.detail:
                notes.append(snapshot.detail)

        if self._transcripts.enabled:
            snapshot = self._transcripts.read()
            if snapshot.primary is not None:
                detail = " · ".join(notes + ([snapshot.detail] if snapshot.detail else []))
                return UsageSnapshot(
                    state=snapshot.state,
                    source=snapshot.source,
                    primary=snapshot.primary,
                    windows=snapshot.windows,
                    detail=detail,
                    tokens=snapshot.tokens,
                )
            if snapshot.detail:
                notes.append(snapshot.detail)

        detail = " · ".join(notes) or "Sem dados de uso disponiveis"
        return UsageSnapshot.unknown(detail)

    @staticmethod
    def _expire_if_stale(snapshot: UsageSnapshot) -> UsageSnapshot:
        """Se a janela ja virou, o percentual em maos e lixo — zera na hora.

        Cobre o intervalo entre o reset acontecer no servidor e a proxima
        resposta da API chegar.
        """
        primary = snapshot.primary
        if primary is None or not primary.is_expired():
            return snapshot
        zeroed = type(primary)(key=primary.key, percent=0.0, resets_at=None)
        return UsageSnapshot(
            state=snapshot.state,
            source=snapshot.source,
            primary=zeroed,
            windows=snapshot.windows,
            detail="Janela reiniciada — aguardando confirmacao da API",
            captured_at=time.time(),
            tokens=snapshot.tokens,
        )
