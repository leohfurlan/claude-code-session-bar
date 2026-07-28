"""Testes das camadas puras: cores, janela de 5h, parsing de API e JSONL.

Rodam em qualquer sistema operacional — nada aqui toca Windows ou rede.

    python -m pytest tests -q
"""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from ctsbar.config import Config
from ctsbar.models import Level, LimitWindow, State, format_duration, level_for
from ctsbar.monitor import UsageMonitor
from ctsbar.sources.api import parse_payload, parse_reset, parse_utilization, select_primary
from ctsbar.sources.transcripts import TranscriptUsageSource, parse_line
from ctsbar.window import UsageEvent, build_blocks, current_block, floor_to_hour

HOUR = 3600


# --------------------------------------------------------------------- cores


def test_faixas_de_cor_nos_limites():
    assert level_for(0, 50, 80) is Level.OK
    assert level_for(49.9, 50, 80) is Level.OK
    assert level_for(50, 50, 80) is Level.WARN
    assert level_for(79.9, 50, 80) is Level.WARN
    assert level_for(80, 50, 80) is Level.DANGER
    assert level_for(100, 50, 80) is Level.DANGER
    assert level_for(None, 50, 80) is Level.NEUTRAL


def test_formato_de_duracao():
    assert format_duration(None) == "--"
    assert format_duration(0) == "<1m"
    assert format_duration(59) == "<1m"
    assert format_duration(60) == "1m"
    assert format_duration(45 * 60) == "45m"
    assert format_duration(2 * HOUR + 13 * 60) == "2h13m"
    assert format_duration(30 * HOUR) == "1d06h"


# ----------------------------------------------------------------- API OAuth


def test_parse_utilization_normaliza_para_0_100():
    assert parse_utilization(42) == 42.0
    assert parse_utilization(0.42, "fraction") == 42.0
    assert parse_utilization(150) == 100.0  # clamp
    assert parse_utilization(-5) == 0.0
    assert parse_utilization(None) is None
    assert parse_utilization("42") is None
    assert parse_utilization(True) is None  # bool nao e numero valido aqui


def test_parse_reset_aceita_epoch_ms_e_iso():
    assert parse_reset(1785000000) == 1785000000.0
    assert parse_reset(1785000000000) == 1785000000.0  # milissegundos
    assert parse_reset("2026-07-28T18:00:00Z") == 1785261600.0
    assert parse_reset("2026-07-28T18:00:00+00:00") == 1785261600.0
    assert parse_reset(None) is None
    assert parse_reset("nao e data") is None


def test_parse_payload_monta_as_janelas():
    payload = {
        "five_hour": {"utilization": 42, "resets_at": "2026-07-28T18:00:00Z"},
        "seven_day": {"utilization": 11, "resets_at": 1785261600},
        "seven_day_sonnet": {"utilization": None},
        "lixo": {"utilization": 99},
    }
    windows = parse_payload(payload)
    assert set(windows) == {"five_hour", "seven_day"}
    assert windows["five_hour"].percent == 42.0
    assert windows["five_hour"].label == "Sessao (5h)"
    assert windows["seven_day"].resets_at == 1785261600.0


def test_parse_payload_tolera_lixo():
    assert parse_payload(None) == {}
    assert parse_payload("texto") == {}
    assert parse_payload({"five_hour": "nao e dict"}) == {}


def test_select_primary():
    windows = {
        "five_hour": LimitWindow("five_hour", 20.0),
        "seven_day": LimitWindow("seven_day", 85.0),
    }
    assert select_primary(windows, "five_hour").key == "five_hour"
    assert select_primary(windows, "worst").key == "seven_day"
    # Metrica indisponivel volta pra sessao de 5h.
    assert select_primary(windows, "seven_day_opus").key == "five_hour"
    assert select_primary({}, "five_hour") is None


# ------------------------------------------------------------ janela de 5h


def test_inicio_da_janela_e_arredondado_na_hora():
    assert floor_to_hour(1785261600 + 42 * 60) == 1785261600


def test_eventos_proximos_ficam_no_mesmo_bloco():
    base = 1785261600  # hora cheia
    events = [UsageEvent(base + 60), UsageEvent(base + HOUR), UsageEvent(base + 2 * HOUR)]
    blocks = build_blocks(events)
    assert len(blocks) == 1
    assert blocks[0].start == base
    assert blocks[0].end == base + 5 * HOUR


def test_evento_alem_de_5h_abre_bloco_novo():
    base = 1785261600
    blocks = build_blocks([UsageEvent(base), UsageEvent(base + 6 * HOUR)])
    assert len(blocks) == 2
    assert blocks[1].start == floor_to_hour(base + 6 * HOUR)


def test_janela_expirada_devolve_none():
    """E isto que faz a barra zerar sozinha quando a janela reseta."""
    base = 1785261600
    events = [UsageEvent(base)]
    assert current_block(events, now=base + HOUR) is not None
    assert current_block(events, now=base + 5 * HOUR + 1) is None
    assert current_block([], now=base) is None


def test_tokens_ponderados():
    event = UsageEvent(0, input_tokens=100, output_tokens=200, cache_read_tokens=10000)
    weights = {"input": 1.0, "output": 5.0, "cache_creation": 1.25, "cache_read": 0.1}
    assert event.raw_tokens == 10300
    assert event.weighted_tokens(weights) == 100 + 1000 + 1000


# ----------------------------------------------------------- transcripts


ASSISTANT_LINE = json.dumps(
    {
        "type": "assistant",
        "timestamp": "2026-07-28T13:32:41.884Z",
        "requestId": "req_abc",
        "message": {
            "id": "msg_1",
            "usage": {
                "input_tokens": 2,
                "output_tokens": 304,
                "cache_creation_input_tokens": 38011,
                "cache_read_input_tokens": 0,
            },
        },
    }
)


def test_parse_line_extrai_tokens():
    key, event = parse_line(ASSISTANT_LINE)
    assert key == "msg_1|req_abc"
    assert event.output_tokens == 304
    assert event.cache_creation_tokens == 38011
    assert event.raw_tokens == 38317


def test_parse_line_ignora_o_que_nao_serve():
    assert parse_line("") is None
    assert parse_line("{json quebrado") is None
    assert parse_line('{"type": "user", "message": {}}') is None
    # Linha cortada no meio (a CLI ainda estava escrevendo).
    assert parse_line(ASSISTANT_LINE[: len(ASSISTANT_LINE) // 2]) is None
    # Assistente sem bloco de usage.
    assert parse_line(json.dumps({"type": "assistant", "message": {"id": "x"}})) is None


def test_dedupe_por_message_id():
    events = {}
    for _ in range(3):
        key, event = parse_line(ASSISTANT_LINE)
        events[key] = event
    assert len(events) == 1


def test_leitura_incremental_so_le_o_que_foi_acrescentado(tmp_path, monkeypatch):
    projects = tmp_path / "projects" / "-home-user-proj"
    projects.mkdir(parents=True)
    transcript = projects / "sessao.jsonl"

    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path))
    monkeypatch.setattr("ctsbar.sources.transcripts.state_path", lambda: tmp_path / "state.json")

    source = TranscriptUsageSource(Config())

    transcript.write_text(ASSISTANT_LINE + "\n", encoding="utf-8")
    assert len(source._read_new_lines(transcript)) == 1
    # Nada novo no arquivo -> nada relido.
    assert source._read_new_lines(transcript) == []

    with transcript.open("a", encoding="utf-8") as handle:
        handle.write(ASSISTANT_LINE + "\n")
    assert len(source._read_new_lines(transcript)) == 1


def test_linha_parcial_e_relida_inteira_depois(tmp_path, monkeypatch):
    projects = tmp_path / "projects" / "-home-user-proj"
    projects.mkdir(parents=True)
    transcript = projects / "sessao.jsonl"

    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path))
    monkeypatch.setattr("ctsbar.sources.transcripts.state_path", lambda: tmp_path / "state.json")
    source = TranscriptUsageSource(Config())

    transcript.write_text(ASSISTANT_LINE[:40], encoding="utf-8")  # sem \n final
    assert source._read_new_lines(transcript) == []

    transcript.write_text(ASSISTANT_LINE + "\n", encoding="utf-8")
    lines = source._read_new_lines(transcript)
    assert len(lines) == 1
    assert parse_line(lines[0]) is not None


def test_totais_sobrevivem_a_reinicio_do_processo(tmp_path, monkeypatch):
    """Offsets persistidos sem os eventos fariam a janela contar pela metade."""
    projects = tmp_path / "projects" / "-home-user-proj"
    projects.mkdir(parents=True)
    transcript = projects / "sessao.jsonl"

    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path))
    monkeypatch.setattr("ctsbar.sources.transcripts.state_path", lambda: tmp_path / "state.json")

    # Timestamp de agora, senao o evento cai fora da janela de 5h.
    now = time.time()
    entry = json.loads(ASSISTANT_LINE)
    entry["timestamp"] = (
        __import__("datetime").datetime.fromtimestamp(now, __import__("datetime").timezone.utc)
        .isoformat()
        .replace("+00:00", "Z")
    )
    transcript.write_text(json.dumps(entry) + "\n", encoding="utf-8")

    config = Config({"fallback": {"token_budget": 100000}})
    first = TranscriptUsageSource(config).read()
    assert first.tokens and first.tokens > 0

    # Segundo processo: le o mesmo estado do disco, sem reler o arquivo.
    second = TranscriptUsageSource(config).read()
    assert second.tokens == first.tokens
    assert second.percent == first.percent


def test_estado_corrompido_forca_releitura_completa(tmp_path, monkeypatch):
    projects = tmp_path / "projects" / "-home-user-proj"
    projects.mkdir(parents=True)
    transcript = projects / "sessao.jsonl"
    transcript.write_text(ASSISTANT_LINE + "\n", encoding="utf-8")

    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path))
    state = tmp_path / "state.json"
    monkeypatch.setattr("ctsbar.sources.transcripts.state_path", lambda: state)

    # Offsets no fim do arquivo, mas sem nenhum evento gravado.
    state.write_text(json.dumps({"offsets": {str(transcript): [9999, 9999]}, "events": {}}))

    source = TranscriptUsageSource(Config())
    assert source._offsets == {}  # descartou e vai reler tudo


def test_sem_pasta_de_projetos_nao_quebra(tmp_path, monkeypatch):
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path / "nao-existe"))
    monkeypatch.setattr("ctsbar.sources.transcripts.state_path", lambda: tmp_path / "state.json")

    snapshot = TranscriptUsageSource(Config()).read()
    assert snapshot.state is State.OK
    assert snapshot.percent == 0.0  # estado zerado, nao erro


# --------------------------------------------------------------- monitor


def test_monitor_sem_credencial_e_sem_transcripts_fica_neutro(tmp_path, monkeypatch):
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path / "vazio"))
    monkeypatch.delenv("CLAUDE_CODE_OAUTH_TOKEN", raising=False)
    monkeypatch.setattr("ctsbar.sources.transcripts.state_path", lambda: tmp_path / "state.json")

    config = Config({"api": {"enabled": False}})
    snapshot = UsageMonitor(config).poll()
    assert snapshot.state in (State.OK, State.UNKNOWN)
    assert snapshot.percent in (0.0, None)


def test_janela_vencida_zera_o_percentual():
    stale = LimitWindow("five_hour", percent=93.0, resets_at=time.time() - 10)
    from ctsbar.models import UsageSnapshot

    snapshot = UsageMonitor._expire_if_stale(
        UsageSnapshot(state=State.OK, source="api", primary=stale)
    )
    assert snapshot.percent == 0.0


def test_janela_valida_nao_e_mexida():
    fresh = LimitWindow("five_hour", percent=93.0, resets_at=time.time() + 600)
    from ctsbar.models import UsageSnapshot

    original = UsageSnapshot(state=State.OK, source="api", primary=fresh)
    assert UsageMonitor._expire_if_stale(original).percent == 93.0


# ---------------------------------------------------------------- config


def test_config_faz_merge_com_os_defaults():
    config = Config({"thresholds": {"warn": 60}})
    assert config.warn == 60.0
    assert config.danger == 80.0  # default preservado
    assert config.get("bar.width") == 190


def test_poll_seconds_tem_piso():
    assert Config({"poll_seconds": 0.1}).poll_seconds == 5.0
    assert Config({"poll_seconds": "invalido"}).poll_seconds == 20.0
    assert Config({"poll_seconds": 25}).poll_seconds == 25.0
