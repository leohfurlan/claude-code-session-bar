"""Isolamento: nenhum teste pode ler nem escrever estado real do usuario.

Sem isto um `last-usage.json` deixado por uma execucao de verdade vaza pra
dentro dos testes e faz o resultado depender da maquina.
"""

from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def isola_estado_em_disco(tmp_path, monkeypatch):
    monkeypatch.setattr("ctsbar.monitor.last_usage_path", lambda: tmp_path / "last-usage.json")
    monkeypatch.setattr(
        "ctsbar.sources.transcripts.state_path", lambda: tmp_path / "read-state.json"
    )
    # Sem CLAUDE_CONFIG_DIR apontado pro tmp, os testes veriam os transcripts
    # reais da maquina que estiver rodando a suite.
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path / "claude"))
    yield
