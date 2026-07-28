"""Thread de polling: decide qual fonte usar e publica o snapshot atual.

Regra: tenta a API (percentual exato); se ela nao entregar, cai pros
transcripts locais (estimativa). Qualquer falha das duas vira estado neutro —
a barra nunca fica sem resposta e o processo nunca morre.
"""

from __future__ import annotations

import json
import threading
import time
from dataclasses import replace
from typing import Callable, List, Optional

from .config import Config
from .models import LimitWindow, State, UsageSnapshot, format_duration
from .paths import last_usage_path
from .sources import ApiUsageSource, TranscriptUsageSource

# Um percentual da API vale sempre nos primeiros minutos.
API_CACHE_SOFT_AGE = 10 * 60

# Depois disso ele so continua valendo enquanto os transcripts locais nao
# acusarem uso novo. Cuidado: essa premissa vale pro Claude Code, mas nao pro
# claude.ai / app de desktop, que gastam a mesma cota sem gerar transcript. Por
# isso o teto duro e curto, e o valor vem marcado com a propria idade.
API_CACHE_HARD_AGE = 30 * 60

# Teto do recuo entre tentativas. Precisa ser bem maior que
# api.interval_seconds, senao o recuo nao recua nada.
API_MAX_BACKOFF = 30 * 60

# Piso entre consultas forcadas pelo usuario, so pra apertar o botao varias
# vezes seguidas nao virar uma rajada.
FORCED_REFRESH_FLOOR = 5


class UsageMonitor:
    """Roda em background e chama `on_update` a cada snapshot novo."""

    def __init__(
        self,
        config: Config,
        on_update: Optional[Callable[[UsageSnapshot], None]] = None,
        on_refresh_failed: Optional[Callable[[str], None]] = None,
    ):
        self.config = config
        self._on_update = on_update
        self._on_refresh_failed = on_refresh_failed
        self._retry_after: Optional[float] = None
        self._api = ApiUsageSource(config)
        self._transcripts = TranscriptUsageSource(config)
        self._snapshot = UsageSnapshot.unknown("Iniciando…")
        self._last_api: Optional[UsageSnapshot] = None
        self._last_api_attempt = 0.0
        self._api_failures = 0
        self._last_activity: Optional[int] = None
        self._activity_at_capture: Optional[int] = None
        self._force_api = False
        self._lock = threading.Lock()
        self._load_last_api()
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
        """Forca uma consulta a API agora ('Atualizar agora' / botao direito).

        Acordar o loop nao basta: o ciclo seguinte bateria na trava de
        intervalo e serviria o cache de novo. Pedido explicito do usuario
        atravessa a trava e zera o recuo acumulado.
        """
        self._force_api = True
        self._api_failures = 0
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
        base = self._setting("api.interval_seconds", 120)
        if self._api_failures == 0:
            return base
        # Retry-After do servidor manda mais que o nosso recuo por dobra.
        if self._retry_after:
            return min(API_MAX_BACKOFF, max(base, self._retry_after))
        return min(API_MAX_BACKOFF, base * (2 ** self._api_failures))

    def _notify_refresh_failed(self, detail: str) -> None:
        if self._on_refresh_failed is None:
            return
        try:
            self._on_refresh_failed(detail)
        except Exception:
            pass

    def _should_call_api(self, now: float, new_activity: bool) -> bool:
        """A API e rate-limited: chamamos devagar, e mais cedo so se houver uso.

        Bater nela a cada ciclo rende HTTP 429 e nenhum dado. O percentual so
        muda quando voce usa o Claude Code, e os transcripts locais avisam
        quando isso acontece.
        """
        if self._last_api_attempt == 0.0:
            return True  # primeira leitura

        elapsed = now - self._last_api_attempt
        # Pedido manual atravessa o intervalo; o piso curto so evita que
        # apertar o botao varias vezes seguidas vire uma rajada.
        if self._force_api and elapsed >= FORCED_REFRESH_FLOOR:
            return True
        if elapsed >= self._api_interval():
            return True

        # Uso novo detectado: antecipa, mas nunca abaixo do piso.
        floor = self._setting("api.min_interval_seconds", 60)
        return new_activity and self._api_failures == 0 and elapsed >= floor

    def _cached_api(self, now: float, activity: Optional[int]) -> Optional[UsageSnapshot]:
        """Ultimo percentual bom, enquanto a API nao responde de novo.

        Vale sempre nos primeiros minutos. Passado isso, so continua valendo
        se nao houve uso novo desde a captura: sem uso, o percentual nao teria
        como ter mudado.
        """
        cached = self._last_api
        if cached is None or cached.primary is None:
            return None

        age = now - cached.captured_at
        if age > API_CACHE_HARD_AGE:
            return None
        # Se a janela virou, o numero guardado nao vale mais nada.
        if cached.primary.is_expired(now):
            return None
        if age > API_CACHE_SOFT_AGE and activity is not None:
            if self._activity_at_capture is None or activity != self._activity_at_capture:
                return None

        detail = cached.detail
        if age >= 90:
            suffix = f"valor de {format_duration(age)} atras"
            detail = f"{detail} · {suffix}" if detail else suffix
        return replace(cached, detail=detail, stale_seconds=age)

    # ------------------------------------------------------- cache em disco

    def _load_last_api(self) -> None:
        """Recupera o ultimo percentual bom gravado por uma execucao anterior."""
        try:
            raw = json.loads(last_usage_path().read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return
        if not isinstance(raw, dict):
            return

        try:
            captured_at = float(raw["captured_at"])
            windows = {
                key: LimitWindow(
                    key=key,
                    percent=None if value.get("percent") is None else float(value["percent"]),
                    resets_at=(
                        None if value.get("resets_at") is None else float(value["resets_at"])
                    ),
                )
                for key, value in raw["windows"].items()
                if isinstance(value, dict)
            }
            primary = windows.get(raw["primary"])
        except (KeyError, TypeError, ValueError, AttributeError):
            return

        if primary is None:
            return

        activity = raw.get("activity")
        self._activity_at_capture = activity if isinstance(activity, int) else None
        self._last_api = UsageSnapshot(
            state=State.OK,
            source="api",
            primary=primary,
            windows=windows,
            captured_at=captured_at,
        )

    def _save_last_api(self, snapshot: UsageSnapshot, activity: Optional[int]) -> None:
        if snapshot.primary is None:
            return

        # A primaria entra sempre, mesmo que `windows` venha vazio: sem ela o
        # arquivo nao teria como ser recarregado.
        windows = dict(snapshot.windows)
        windows.setdefault(snapshot.primary.key, snapshot.primary)

        try:
            last_usage_path().parent.mkdir(parents=True, exist_ok=True)
            last_usage_path().write_text(
                json.dumps(
                    {
                        "captured_at": snapshot.captured_at,
                        "primary": snapshot.primary.key,
                        "activity": activity,
                        "windows": {
                            key: {"percent": window.percent, "resets_at": window.resets_at}
                            for key, window in windows.items()
                        },
                    }
                ),
                encoding="utf-8",
            )
        except OSError:
            pass

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
            forced, self._force_api = self._force_api, False
            snapshot = self._api.read()
            if snapshot.state is State.OK and snapshot.primary is not None:
                self._api_failures = 0
                self._retry_after = None
                self._last_activity = activity
                self._activity_at_capture = activity
                self._last_api = replace(snapshot, captured_at=now)
                self._save_last_api(self._last_api, activity)
                return self._expire_if_stale(self._last_api)
            self._api_failures += 1
            # Se o servidor disse quando voltar, obedecemos em vez de chutar.
            self._retry_after = getattr(self._api, "last_retry_after", None)
            if snapshot.detail:
                notes.append(snapshot.detail)
            if forced:
                # Sem isto o usuario aperta "Atualizar agora", nada muda e ele
                # nao tem como saber que a API recusou.
                self._notify_refresh_failed(snapshot.detail or "A API nao respondeu")

        cached = self._cached_api(now, activity)
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
