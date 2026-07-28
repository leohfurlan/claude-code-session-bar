"""Thread de polling: decide qual fonte usar e publica o snapshot atual.

Regra: tenta a API (percentual exato); se ela nao entregar, cai pros
transcripts locais (estimativa). Qualquer falha das duas vira estado neutro —
a barra nunca fica sem resposta e o processo nunca morre.
"""

from __future__ import annotations

import threading
import time
from dataclasses import replace
from typing import Callable, List, Optional

from .config import Config
from .models import State, UsageSnapshot, format_duration
from .sources import ApiUsageSource, TranscriptUsageSource

# Quanto tempo um percentual da API continua valendo depois que ela para de
# responder. Um numero de alguns minutos atras ainda diz muito mais sobre o
# limite do que uma contagem de tokens.
API_CACHE_MAX_AGE = 10 * 60

# Teto do recuo entre tentativas quando a API falha em sequencia.
API_MAX_BACKOFF = 5 * 60


class UsageMonitor:
    """Roda em background e chama `on_update` a cada snapshot novo."""

    def __init__(self, config: Config, on_update: Optional[Callable[[UsageSnapshot], None]] = None):
        self.config = config
        self._on_update = on_update
        self._api = ApiUsageSource(config)
        self._transcripts = TranscriptUsageSource(config)
        self._snapshot = UsageSnapshot.unknown("Iniciando…")
        self._last_api: Optional[UsageSnapshot] = None
        self._last_api_attempt = 0.0
        self._api_failures = 0
        self._last_activity: Optional[int] = None
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

    def _setting(self, key: str, default: float) -> float:
        """Le um numero da config. `0` e valor legitimo, nao ausencia."""
        value = self.config.get(key, default)
        try:
            return max(0.0, float(value))
        except (TypeError, ValueError):
            return default

    def _api_interval(self) -> float:
        """Espera ate a proxima chamada, ja considerando o recuo por falha."""
        base = self._setting("api.interval_seconds", 300)
        if self._api_failures == 0:
            return base
        return min(API_MAX_BACKOFF, base * (2 ** self._api_failures))

    def _should_call_api(self, now: float, new_activity: bool) -> bool:
        """A API e rate-limited: chamamos devagar, e mais cedo so se houver uso.

        Bater nela a cada ciclo rende HTTP 429 e nenhum dado. O percentual so
        muda quando voce usa o Claude Code, e os transcripts locais avisam
        quando isso acontece.
        """
        if self._last_api_attempt == 0.0:
            return True  # primeira leitura

        elapsed = now - self._last_api_attempt
        if elapsed >= self._api_interval():
            return True

        # Uso novo detectado: antecipa, mas nunca abaixo do piso.
        floor = self._setting("api.min_interval_seconds", 60)
        return new_activity and self._api_failures == 0 and elapsed >= floor

    def _cached_api(self, now: float) -> Optional[UsageSnapshot]:
        """Ultimo percentual bom, enquanto a API nao responde de novo."""
        cached = self._last_api
        if cached is None or cached.primary is None:
            return None

        age = now - cached.captured_at
        if age > API_CACHE_MAX_AGE:
            return None
        # Se a janela virou, o numero guardado nao vale mais nada.
        if cached.primary.is_expired(now):
            return None

        detail = cached.detail
        if age >= 90:
            suffix = f"valor de {format_duration(age)} atras"
            detail = f"{detail} · {suffix}" if detail else suffix
        return replace(cached, detail=detail)

    def poll(self) -> UsageSnapshot:
        """Um ciclo de leitura. Nunca levanta excecao."""
        now = time.time()
        notes: List[str] = []

        # Os transcripts rodam sempre: a leitura e incremental (barata) e serve
        # de sinal de atividade pra decidir se vale reconsultar a API.
        local = self._transcripts.read() if self._transcripts.enabled else None
        activity = None if local is None else local.tokens
        new_activity = activity is not None and activity != self._last_activity

        if self._api.enabled and self._should_call_api(now, new_activity):
            self._last_api_attempt = now
            snapshot = self._api.read()
            if snapshot.state is State.OK and snapshot.primary is not None:
                self._api_failures = 0
                self._last_activity = activity
                self._last_api = replace(snapshot, captured_at=now)
                return self._expire_if_stale(self._last_api)
            self._api_failures += 1
            if snapshot.detail:
                notes.append(snapshot.detail)

        cached = self._cached_api(now)
        if cached is not None:
            return cached

        if local is not None and local.primary is not None:
            detail = " · ".join(notes + ([local.detail] if local.detail else []))
            return replace(local, detail=detail)
        if local is not None and local.detail:
            notes.append(local.detail)

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
