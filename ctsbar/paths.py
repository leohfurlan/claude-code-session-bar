"""Descoberta dos diretorios do Claude Code e do proprio app.

O Claude Code guarda tudo em `%USERPROFILE%\\.claude` no Windows (ou no
caminho apontado por CLAUDE_CONFIG_DIR, que a CLI respeita):

    .claude/
      .credentials.json          <- OAuth em JSON puro (no Windows)
      projects/
        <cwd-com-hifens>/
          <session-uuid>.jsonl   <- transcript, uma entrada por linha
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Iterator, Optional


def claude_config_dir() -> Path:
    """Diretorio de configuracao do Claude Code."""
    override = os.environ.get("CLAUDE_CONFIG_DIR")
    if override:
        return Path(override).expanduser()
    return Path.home() / ".claude"


def credentials_path() -> Path:
    return claude_config_dir() / ".credentials.json"


def projects_dir() -> Path:
    return claude_config_dir() / "projects"


def iter_transcripts(max_age_seconds: Optional[float] = None) -> Iterator[Path]:
    """Lista os arquivos .jsonl de transcript, opcionalmente so os recentes.

    O filtro por mtime evita varrer meses de historico a cada poll.
    """
    root = projects_dir()
    if not root.is_dir():
        return
    import time

    cutoff = None if max_age_seconds is None else time.time() - max_age_seconds
    try:
        entries = list(root.glob("*/*.jsonl"))
    except OSError:
        return
    for path in entries:
        try:
            if cutoff is not None and path.stat().st_mtime < cutoff:
                continue
        except OSError:
            continue
        yield path


def app_config_dir() -> Path:
    """Onde a barra guarda a propria config (nao mexe na pasta do Claude)."""
    base = os.environ.get("APPDATA")
    if base:
        return Path(base) / "ctsbar"
    return Path.home() / ".config" / "ctsbar"


def app_config_path() -> Path:
    return app_config_dir() / "config.json"


def state_path() -> Path:
    """Cache de offsets de leitura dos transcripts (leitura incremental)."""
    return app_config_dir() / "read-state.json"


def last_usage_path() -> Path:
    """Ultimo percentual bom vindo da API.

    Guardado em disco pra que reiniciar o app nao volte a mostrar contagem de
    tokens enquanto a API (que e rate-limited) nao libera a proxima consulta.
    """
    return app_config_dir() / "last-usage.json"
