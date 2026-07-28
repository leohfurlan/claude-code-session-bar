"""Fonte primaria: endpoint de uso do proprio Claude Code.

O Claude Code alimenta o `/usage` com:

    GET https://api.anthropic.com/api/oauth/usage
    Authorization: Bearer <accessToken>
    anthropic-beta: oauth-2025-04-20

A resposta traz uma entrada por janela de limite:

    {
      "five_hour":         {"utilization": 42, "resets_at": ...},
      "seven_day":         {"utilization": 11, "resets_at": ...},
      "seven_day_opus":    {...},
      "seven_day_sonnet":  {...}
    }

`utilization` vem em 0-100 e `resets_at` pode ser epoch (s ou ms) ou ISO-8601,
entao os dois formatos sao aceitos. O token sai de CLAUDE_CODE_OAUTH_TOKEN ou
de `%USERPROFILE%\\.claude\\.credentials.json` (`claudeAiOauth.accessToken`),
que e onde a CLI grava no Windows.

Este modulo so le: nunca escreve nem renova credenciais. Se o token estiver
vencido, a chamada falha e o monitor cai pro fallback.
"""

from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from typing import Any, Dict, Optional, Tuple

from ..models import UsageSnapshot, LimitWindow, State
from ..paths import credentials_path

OAUTH_BETA = "oauth-2025-04-20"
USER_AGENT = "ctsbar/1.0 (claude-code-session-bar)"
KNOWN_WINDOWS = ("five_hour", "seven_day", "seven_day_opus", "seven_day_sonnet")


class AuthError(Exception):
    """Sem token utilizavel."""


def read_access_token() -> Tuple[Optional[str], Optional[float]]:
    """Devolve (token, expires_at_epoch_segundos).

    Precedencia igual a da CLI: variavel de ambiente primeiro, arquivo depois.
    """
    env_token = os.environ.get("CLAUDE_CODE_OAUTH_TOKEN")
    if env_token:
        return env_token.strip(), None

    try:
        raw = json.loads(credentials_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None, None

    oauth = raw.get("claudeAiOauth") if isinstance(raw, dict) else None
    if not isinstance(oauth, dict):
        return None, None

    token = oauth.get("accessToken")
    if not isinstance(token, str) or not token:
        return None, None

    expires_at = oauth.get("expiresAt")
    # A CLI grava expiresAt em milissegundos.
    if isinstance(expires_at, (int, float)) and expires_at > 0:
        return token, float(expires_at) / 1000.0
    return token, None


def parse_reset(value: Any) -> Optional[float]:
    """Normaliza resets_at pra epoch em segundos, aceitando varios formatos."""
    if value is None:
        return None
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        # Heuristica: acima de 1e11 so pode ser milissegundos.
        return float(value) / 1000.0 if value > 1e11 else float(value)
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return None
        if text.replace(".", "", 1).isdigit():
            return parse_reset(float(text))
        try:
            # fromisoformat do 3.9/3.10 nao aceita o sufixo 'Z'.
            parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
        except ValueError:
            return None
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.timestamp()
    return None


def parse_utilization(value: Any, scale: str = "percent") -> Optional[float]:
    """Normaliza utilization pra 0-100."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    percent = float(value) * 100.0 if scale == "fraction" else float(value)
    return max(0.0, min(100.0, percent))


def parse_payload(payload: Any, scale: str = "percent") -> Dict[str, LimitWindow]:
    """Converte o JSON da API no dicionario de janelas."""
    if not isinstance(payload, dict):
        return {}

    windows: Dict[str, LimitWindow] = {}
    for key in KNOWN_WINDOWS:
        entry = payload.get(key)
        if not isinstance(entry, dict):
            continue
        percent = parse_utilization(entry.get("utilization"), scale)
        if percent is None:
            continue
        windows[key] = LimitWindow(
            key=key,
            percent=percent,
            resets_at=parse_reset(entry.get("resets_at") or entry.get("resetsAt")),
        )
    return windows


class ApiUsageSource:
    """Le o percentual exato de uso pela API OAuth do Claude Code."""

    name = "api"

    def __init__(self, config):
        self.config = config

    @property
    def enabled(self) -> bool:
        return bool(self.config.get("api.enabled", True))

    def _fetch(self) -> Any:
        token, expires_at = read_access_token()
        if not token:
            raise AuthError(
                "Sem credencial do Claude Code (faca login com `claude` uma vez)"
            )
        # Token vencido ainda e tentado: a CLI renova em background e o arquivo
        # pode estar mais novo do que o expiresAt que acabamos de ler.
        if expires_at is not None and expires_at < time.time():
            pass

        base = (self.config.get("api.base_url") or "https://api.anthropic.com").rstrip("/")
        request = urllib.request.Request(
            f"{base}/api/oauth/usage",
            headers={
                "Authorization": f"Bearer {token}",
                "anthropic-beta": OAUTH_BETA,
                "Content-Type": "application/json",
                "User-Agent": USER_AGENT,
                "Accept": "application/json",
            },
            method="GET",
        )
        timeout = float(self.config.get("api.timeout_seconds", 6) or 6)
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return json.loads(response.read().decode("utf-8", errors="replace"))

    def read(self) -> UsageSnapshot:
        """Nunca levanta excecao: erro vira snapshot de erro."""
        if not self.enabled:
            return UsageSnapshot.unknown("Fonte API desativada", source=self.name)

        try:
            payload = self._fetch()
        except AuthError as exc:
            return UsageSnapshot.error(str(exc), source=self.name)
        except urllib.error.HTTPError as exc:
            if exc.code in (401, 403):
                detail = "Credencial expirada ou sem permissao (rode `claude` pra renovar)"
            elif exc.code == 429:
                detail = "API respondeu 429 (limite atingido) — tentando de novo no proximo ciclo"
            else:
                detail = f"API respondeu HTTP {exc.code}"
            return UsageSnapshot.error(detail, source=self.name)
        except urllib.error.URLError as exc:
            return UsageSnapshot.error(f"Sem rede: {exc.reason}", source=self.name)
        except (ValueError, OSError) as exc:
            return UsageSnapshot.error(f"Resposta invalida da API: {exc}", source=self.name)

        windows = parse_payload(payload, self.config.get("api.utilization_scale", "percent"))
        if not windows:
            return UsageSnapshot.unknown(
                "API respondeu sem dados de uso (nenhuma sessao registrada)",
                source=self.name,
            )

        return UsageSnapshot(
            state=State.OK,
            source=self.name,
            primary=select_primary(windows, self.config.get("metric", "five_hour")),
            windows=windows,
        )


def select_primary(windows: Dict[str, LimitWindow], metric: str) -> Optional[LimitWindow]:
    """Escolhe a janela que a barra desenha.

    `metric` pode ser uma chave conhecida ou "worst" (a mais consumida).
    """
    if not windows:
        return None
    if metric == "worst":
        return max(windows.values(), key=lambda w: w.percent if w.percent is not None else -1.0)
    if metric in windows:
        return windows[metric]
    # Metrica pedida indisponivel: cai pra sessao de 5h, depois pra qualquer uma.
    return windows.get("five_hour") or next(iter(windows.values()))
