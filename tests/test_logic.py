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
        # utilization nulo nao vira janela...
        "seven_day_sonnet": {"utilization": None},
        # ...mas chave desconhecida com numero valido vira, sim: a API manda
        # janelas alem das documentadas e novas surgem sem aviso.
        "cinder_cove": {"utilization": 99},
    }
    windows = parse_payload(payload)
    assert set(windows) == {"five_hour", "seven_day", "cinder_cove"}
    assert windows["five_hour"].percent == 42.0
    assert windows["five_hour"].label == "Sessao (5h)"
    assert windows["seven_day"].resets_at == 1785261600.0


def test_parse_payload_com_a_resposta_real_da_api():
    """Payload real capturado do endpoint, com as chaves que ele de fato manda."""
    payload = {
        "five_hour": {
            "utilization": 80.0,
            "resets_at": "2026-07-28T18:09:59.165131+00:00",
            "limit_dollars": None,
            "used_dollars": None,
        },
        "seven_day": {"utilization": 14.0, "resets_at": "2026-08-03T14:00:00.165163+00:00"},
        # Janelas que nao se aplicam ao plano vem nulas.
        "seven_day_opus": None,
        "seven_day_sonnet": None,
        "seven_day_cowork": None,
        "tangelo": None,
        # Forma diferente: nao tem utilization, nao vira janela.
        "extra_usage": {"is_enabled": False, "monthly_limit": None},
    }
    windows = parse_payload(payload)

    assert set(windows) == {"five_hour", "seven_day"}
    assert windows["five_hour"].percent == 80.0
    # ISO-8601 com microssegundos e offset explicito.
    assert windows["five_hour"].resets_at == 1785262199.165131
    # 80% cai na faixa vermelha.
    assert level_for(windows["five_hour"].percent, 50, 80) is Level.DANGER


def test_janela_desconhecida_nao_e_descartada():
    """Chaves novas aparecem sem aviso; melhor mostrar do que sumir com o dado."""
    windows = parse_payload({"seven_day_cowork": {"utilization": 22.0}})
    assert windows["seven_day_cowork"].percent == 22.0
    assert windows["seven_day_cowork"].label == "Semana (Cowork)"

    inedita = parse_payload({"nimbus_quill": {"utilization": 5.0}})
    assert inedita["nimbus_quill"].label == "Nimbus quill"


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


class _FakeApi:
    """Fonte de API controlavel, pra simular 429 e recuperacao."""

    name = "api"
    enabled = True

    def __init__(self, *respostas):
        self.respostas = list(respostas)
        self.chamadas = 0

    def read(self):
        from ctsbar.models import UsageSnapshot

        self.chamadas += 1
        valor = self.respostas.pop(0) if self.respostas else self.respostas_padrao
        if isinstance(valor, str):
            return UsageSnapshot.error(valor, source="api")
        return UsageSnapshot(
            state=State.OK,
            source="api",
            primary=LimitWindow("five_hour", valor, resets_at=time.time() + 3600),
        )

    respostas_padrao = "API respondeu 429 (limite atingido)"


def _monitor_com_api(fake, **config_extra):
    """Monitor com a API simulada e o fallback desligado, pra isolar a API."""
    monitor = UsageMonitor(Config({"api": config_extra, "fallback": {"enabled": False}}))
    monitor._api = fake
    return monitor


def test_api_nao_e_consultada_a_cada_ciclo():
    """Bater no endpoint todo ciclo rende 429 — o valor bom fica em cache."""
    fake = _FakeApi(34.0)
    monitor = _monitor_com_api(fake)

    primeiro = monitor.poll()
    assert primeiro.percent == 34.0
    assert fake.chamadas == 1

    # Ciclos seguintes, sem atividade nova: serve o cache, sem tocar na API.
    for _ in range(5):
        assert monitor.poll().percent == 34.0
    assert fake.chamadas == 1


def test_429_mantem_o_ultimo_percentual_em_vez_de_cair_pra_tokens():
    fake = _FakeApi(34.0, "API respondeu 429 (limite atingido)")
    monitor = _monitor_com_api(fake, interval_seconds=0)

    assert monitor.poll().percent == 34.0
    depois = monitor.poll()  # agora a API falha
    assert fake.chamadas == 2
    assert depois.percent == 34.0  # segurou o valor bom
    assert depois.source == "api"


def test_falhas_seguidas_aumentam_o_recuo():
    fake = _FakeApi("erro", "erro", "erro")
    monitor = _monitor_com_api(fake, interval_seconds=10)

    assert monitor._api_interval() == 10  # sem falhas
    monitor.poll()
    assert monitor._api_interval() == 20
    monitor._last_api_attempt = 0.0  # libera a proxima tentativa
    monitor.poll()
    assert monitor._api_interval() == 40


def test_atualizar_agora_atravessa_o_intervalo():
    """Sem isto o botao so acorda o loop, que serve o cache de novo."""
    fake = _FakeApi(45.0, 50.0)
    monitor = _monitor_com_api(fake, interval_seconds=3600)

    assert monitor.poll().percent == 45.0
    assert monitor.poll().percent == 45.0  # trava do intervalo: cache
    assert fake.chamadas == 1

    monitor.refresh_now()
    monitor._last_api_attempt = time.time() - 10  # passa o piso curto
    assert monitor.poll().percent == 50.0
    assert fake.chamadas == 2


def test_atualizar_agora_zera_o_recuo_acumulado():
    monitor = _monitor_com_api(_FakeApi("erro"), interval_seconds=10)
    monitor.poll()
    assert monitor._api_failures == 1

    monitor.refresh_now()
    assert monitor._api_failures == 0
    assert monitor._force_api is True


def test_valor_do_cache_vem_marcado_como_velho():
    """A barra mostra '~' — quem olha de relance nao ve o tooltip."""
    from ctsbar.models import UsageSnapshot

    monitor = _monitor_com_api(_FakeApi())
    monitor._last_api = UsageSnapshot(
        state=State.OK,
        source="api",
        primary=LimitWindow("five_hour", 45.0, resets_at=time.time() + 3600),
        captured_at=time.time() - 240,
    )
    cached = monitor._cached_api(time.time(), None)
    assert cached.is_stale
    assert cached.stale_seconds >= 240

    # Valor recem lido da API nao e marcado.
    fresh = _monitor_com_api(_FakeApi(50.0)).poll()
    assert not fresh.is_stale
    assert fresh.stale_seconds is None


def test_parse_retry_after_aceita_segundos_e_data():
    from ctsbar.sources.api import parse_retry_after

    assert parse_retry_after({"retry-after": "120"}) == 120.0
    assert parse_retry_after({"Retry-After": "45"}) == 45.0
    assert parse_retry_after({}) is None
    assert parse_retry_after(None) is None
    assert parse_retry_after({"retry-after": "lixo"}) is None

    futuro = parse_retry_after({"retry-after": "Wed, 29 Jul 2026 12:00:00 GMT"})
    assert futuro is not None and futuro >= 0


def test_retry_after_do_servidor_manda_no_recuo():
    """Obedecer o servidor é melhor que chutar um recuo próprio."""
    monitor = _monitor_com_api(_FakeApi("erro"), interval_seconds=120)
    monitor._api_failures = 1

    monitor._retry_after = None
    assert monitor._api_interval() == 240  # dobra padrao

    monitor._retry_after = 900
    assert monitor._api_interval() == 900  # servidor pediu mais

    monitor._retry_after = 30
    assert monitor._api_interval() == 120  # nunca abaixo do intervalo base


def test_falha_no_refresh_manual_avisa_o_usuario():
    """Sem aviso, apertar o botao e nada mudar parece um botao quebrado."""
    avisos = []
    monitor = UsageMonitor(
        Config({"api": {"interval_seconds": 120}, "fallback": {"enabled": False}}),
        on_refresh_failed=avisos.append,
    )
    monitor._api = _FakeApi("API respondeu 429 (consultas demais)")

    monitor.poll()  # primeira leitura, automatica: nao avisa
    assert avisos == []

    monitor.refresh_now()
    monitor._last_api_attempt = time.time() - 10
    monitor.poll()
    assert len(avisos) == 1
    assert "429" in avisos[0]


def test_o_recuo_precisa_de_fato_recuar():
    """Com o teto igual ao intervalo base, o recuo seria um no-op."""
    from ctsbar.monitor import API_MAX_BACKOFF

    padrao = Config().get("api.interval_seconds")
    assert API_MAX_BACKOFF > padrao

    monitor = _monitor_com_api(_FakeApi("erro"))
    assert monitor._api_interval() == padrao
    monitor._api_failures = 1
    assert monitor._api_interval() > padrao


def test_cache_expira_e_nao_engana():
    from ctsbar.models import UsageSnapshot

    monitor = _monitor_com_api(_FakeApi())
    monitor._last_api = UsageSnapshot(
        state=State.OK,
        source="api",
        primary=LimitWindow("five_hour", 34.0, resets_at=time.time() + 3600),
        captured_at=time.time() - 3600,  # bem velho
    )
    assert monitor._cached_api(time.time(), None) is None


def test_cache_e_descartado_quando_a_janela_vira():
    from ctsbar.models import UsageSnapshot

    monitor = _monitor_com_api(_FakeApi())
    monitor._last_api = UsageSnapshot(
        state=State.OK,
        source="api",
        primary=LimitWindow("five_hour", 93.0, resets_at=time.time() - 1),
    )
    assert monitor._cached_api(time.time(), None) is None


def test_percentual_sobrevive_a_reinicio_do_app(tmp_path, monkeypatch):
    """Reiniciar não pode voltar a mostrar tokens enquanto a API não libera."""
    monkeypatch.setattr("ctsbar.monitor.last_usage_path", lambda: tmp_path / "last.json")

    primeiro = _monitor_com_api(_FakeApi(34.0))
    assert primeiro.poll().percent == 34.0

    # Processo novo: a API so devolve 429 daqui pra frente.
    segundo = _monitor_com_api(_FakeApi("API respondeu 429 (limite atingido)"))
    depois = segundo.poll()
    assert depois.percent == 34.0
    assert depois.source == "api"


def test_cache_velho_vale_enquanto_nao_houve_uso_novo():
    from ctsbar.models import UsageSnapshot

    monitor = _monitor_com_api(_FakeApi())
    monitor._last_api = UsageSnapshot(
        state=State.OK,
        source="api",
        primary=LimitWindow("five_hour", 34.0, resets_at=time.time() + 3600),
        captured_at=time.time() - 25 * 60,  # alem do periodo curto
    )
    monitor._activity_at_capture = 1000

    # Mesma atividade -> o percentual nao teria como ter mudado.
    assert monitor._cached_api(time.time(), 1000).percent == 34.0
    # Uso novo -> o valor guardado nao vale mais.
    assert monitor._cached_api(time.time(), 1500) is None


def test_cache_avisa_a_idade_do_valor():
    from ctsbar.models import UsageSnapshot

    monitor = _monitor_com_api(_FakeApi())
    monitor._last_api = UsageSnapshot(
        state=State.OK,
        source="api",
        primary=LimitWindow("five_hour", 34.0, resets_at=time.time() + 3600),
        captured_at=time.time() - 240,
    )
    assert "atras" in monitor._cached_api(time.time(), None).detail


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


# -------------------------------------------------------------- bandeja


def _tray(snapshot):
    """TrayIcon sem pystray instalado: da pra testar os callables mesmo assim."""
    from ctsbar.ui.tray import TrayIcon

    nada = lambda: None  # noqa: E731
    return TrayIcon(
        Config(),
        get_snapshot=lambda: snapshot,
        on_refresh=nada,
        on_toggle_bar=nada,
        on_reset_position=nada,
        on_toggle_autostart=nada,
        on_open_config=nada,
        on_quit=nada,
        is_autostart_enabled=lambda: False,
        is_bar_visible=lambda: True,
    )


def test_textos_do_menu_aceitam_a_aridade_do_pystray():
    """O pystray chama o callable de texto passando o proprio item."""
    from ctsbar.models import UsageSnapshot

    snapshot = UsageSnapshot(
        state=State.OK,
        source="api",
        primary=LimitWindow("five_hour", 34.0, resets_at=time.time() + 3600),
    )
    tray = _tray(snapshot)

    # Sem argumento e com argumento (o MenuItem) — as duas formas.
    for texto in (tray._header_text, tray._source_text):
        sem_arg = texto()
        com_arg = texto(object())
        assert sem_arg == com_arg
        assert isinstance(sem_arg, str) and sem_arg

    assert "34%" in tray._header_text()
    assert "api" in tray._source_text().lower()


def test_header_sem_sessao_nao_quebra():
    from ctsbar.models import UsageSnapshot

    tray = _tray(UsageSnapshot.unknown("sem dados"))
    assert tray._header_text(object()) == "Sem sessao ativa"
    assert isinstance(tray._tooltip(UsageSnapshot.unknown("x")), str)


def test_tooltip_respeita_o_limite_do_windows():
    from ctsbar.models import UsageSnapshot

    snapshot = UsageSnapshot(
        state=State.OK,
        source="api",
        primary=LimitWindow("five_hour", 34.0, resets_at=time.time() + 3600),
        windows={
            "five_hour": LimitWindow("five_hour", 34.0),
            "seven_day": LimitWindow("seven_day", 10.0),
            "seven_day_opus": LimitWindow("seven_day_opus", 55.0),
        },
        detail="detalhe bem comprido " * 20,
    )
    assert len(_tray(snapshot)._tooltip(snapshot)) <= 127


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
