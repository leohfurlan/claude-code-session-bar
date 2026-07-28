# Claude Code Session Bar

Barrinha de uso da sessão do Claude Code para Windows 11. Fica sempre visível
encostada na taskbar, muda de cor conforme o consumo sobe e mostra quanto falta
pro reset da janela — sem precisar abrir nada.

```
┌──────────────────────────────┐
│████████████████░░░░░░  62%  2h13m │   ← amarelo: entre 50% e 80%
└──────────────────────────────┘
```

- **Verde** abaixo de 50% · **Amarelo** de 50% a 80% · **Vermelho** acima de 80%
- Atualiza sozinha a cada 20 s (configurável entre 15 e 30 s)
- Um processo só, duas dependências, ~15 MB de RAM e CPU ~0% ocioso
- Ícone na bandeja com o mesmo percentual e o menu de controle
- Não trava nem dá erro com o Claude Code fechado: mostra estado neutro

---

## De onde vêm os dados

Antes de codar eu investiguei onde o Claude Code guarda uso e sessão. O
resultado está aqui porque ele explica as decisões do projeto.

### Fonte primária — API OAuth (percentual exato)

O próprio Claude Code alimenta o comando `/usage` com uma chamada HTTP:

```
GET https://api.anthropic.com/api/oauth/usage
Authorization: Bearer <accessToken>
anthropic-beta: oauth-2025-04-20
```

A resposta traz uma entrada por janela de limite:

```json
{
  "five_hour":        { "utilization": 42, "resets_at": "..." },
  "seven_day":        { "utilization": 11, "resets_at": "..." },
  "seven_day_opus":   { "utilization": 30, "resets_at": "..." },
  "seven_day_sonnet": { "utilization":  8, "resets_at": "..." }
}
```

`utilization` vem em 0–100 e é exatamente o número que o `/usage` mostra. É a
fonte que a barra usa por padrão — nada de estimativa.

O token sai de `%USERPROFILE%\.claude\.credentials.json`:

```json
{ "claudeAiOauth": { "accessToken": "...", "expiresAt": 1785261600000, "...": "..." } }
```

No Windows esse arquivo é JSON puro (o Keychain só é usado no macOS). A variável
`CLAUDE_CODE_OAUTH_TOKEN` tem precedência, igual na CLI. **A barra só lê**: nunca
escreve nem renova credencial. Quem renova é o Claude Code, e a barra pega a
versão nova na leitura seguinte.

### Fonte de fallback — transcripts JSONL (estimativa)

Sem rede ou sem credencial válida, a barra cai pro histórico local:

```
%USERPROFILE%\.claude\projects\<cwd-com-hifens>\<session-uuid>.jsonl
```

Um JSON por linha. As linhas que interessam são as respostas do assistente:

```json
{"type":"assistant","timestamp":"2026-07-28T13:32:41.884Z","requestId":"req_...",
 "message":{"id":"msg_...","usage":{"input_tokens":2,"output_tokens":304,
 "cache_creation_input_tokens":38011,"cache_read_input_tokens":0}}}
```

É a mesma matéria-prima que o [ccusage](https://github.com/ryoppippi/ccusage)
usa. A abordagem serviu de referência; o código aqui é escrito do zero.

A leitura é **incremental**: os offsets já lidos ficam em
`%APPDATA%\ctsbar\read-state.json` e cada ciclo processa só o que foi
acrescentado — por isso a CPU fica no chão mesmo com histórico grande.

### A janela de limite

O Claude Code trabalha com duas janelas rolantes, e os valores estão fixos no
próprio binário da CLI (`five_hour` / `seven_day`):

| Janela | Duração | Chave na API |
|---|---|---|
| Sessão | **5 h** (18 000 s) | `five_hour` |
| Semanal | **7 dias** (604 800 s) | `seven_day`, `seven_day_opus`, `seven_day_sonnet` |

A janela de sessão abre no primeiro uso depois de um período ocioso e fecha 5 h
depois; o uso de todas as superfícies (claude.ai, Claude Code, Desktop) conta
pro mesmo limite. Como o `resets_at` vem pronto na API, a barra não precisa
adivinhar nada — e quando o horário passa, ela zera na hora, sem esperar a
próxima resposta.

No fallback (offline) a janela é reconstruída localmente: abre no primeiro
evento, arredondado pra hora cheia, e dura 5 h. É uma aproximação, porque a
janela verdadeira é do lado do servidor.

---

## Instalação

Precisa de Python 3.9+ (o instalador oficial do [python.org](https://www.python.org/downloads/windows/)
já traz o tkinter). **Não precisa de administrador.**

```powershell
git clone https://github.com/leohfurlan/use-token-session-bar.git
cd use-token-session-bar

py -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
```

As duas dependências são `pystray` e `Pillow`, só pro ícone da bandeja. A barra
em si usa `tkinter` e `ctypes`, que são da biblioteca padrão.

### Confira se está lendo o uso

```powershell
python -m ctsbar --once
```

Saída esperada com credencial válida:

```
 42.0%  Sessao (5h)                     reseta em   2h13m  [ok]  (fonte: api)
        Semana (todos os modelos): 11.0%
```

Se aparecer `(fonte: transcripts)`, a API não respondeu — o motivo vem escrito
na segunda linha. Veja [Problemas comuns](#problemas-comuns).

---

## Rodando

**Em background, sem janela de console** (é o modo normal):

```powershell
run.bat
```

**Com terminal**, útil pra ver o que está acontecendo:

```powershell
python -m ctsbar
```

**Modo texto**, sem interface gráfica:

```powershell
python -m ctsbar --watch
```

### Iniciar com o Windows

Pelo menu da bandeja: clique com o botão direito no ícone → **Iniciar com o
Windows**. Ou pela linha de comando:

```powershell
python -m ctsbar --enable-autostart
python -m ctsbar --disable-autostart
```

Grava um valor em `HKCU\Software\Microsoft\Windows\CurrentVersion\Run` apontando
pro `pythonw.exe run.pyw`. É por usuário, não pede administrador e sai limpo
quando desligado.

### Usando

| Ação | Onde |
|---|---|
| Mover a barra | arrastar com o botão esquerdo (a posição fica salva) |
| Esconder a barra | duplo clique nela, ou o menu da bandeja |
| Atualizar na hora | botão direito na barra |
| Voltar pro canto padrão | menu → **Reposicionar barra** |
| Forçar atualização | menu → **Atualizar agora** |
| Editar as configurações | menu → **Abrir config.json** |

---

## Configuração

Fica em `%APPDATA%\ctsbar\config.json`. Crie o arquivo com os padrões:

```powershell
python -m ctsbar --write-config
```

```jsonc
{
  "poll_seconds": 20,              // intervalo de atualização (mínimo 5)
  "metric": "five_hour",           // five_hour | seven_day | seven_day_opus
                                   // | seven_day_sonnet | worst
  "thresholds": { "warn": 50.0, "danger": 80.0 },

  "bar": {
    "width": 190, "height": 18,
    "margin_x": 12, "margin_y": 2, // distância da borda quando não arrastada
    "x": null, "y": null,          // posição fixa (preenchida ao arrastar)
    "opacity": 0.92,
    "show_text": true,
    "visible": true,
    "font_size": 8
  },

  "taskbar_embed": false,          // experimental, veja abaixo
  "api": { "enabled": true, "timeout_seconds": 6 },
  "fallback": { "enabled": true, "token_budget": null },

  "colors": {
    "ok": "#2ecc71", "warn": "#f1c40f", "danger": "#e74c3c",
    "neutral": "#5a5a5a", "background": "#1e1e1e",
    "text": "#f0f0f0", "border": "#3c3c3c"
  }
}
```

`metric: "worst"` faz a barra mostrar sempre a janela mais consumida — útil pra
não ser pego de surpresa pelo limite semanal enquanto a sessão está tranquila.

Alterações valem no próximo ciclo, sem reiniciar (posição e tamanho da janela
precisam de reinício).

### Embutir de verdade na taskbar

Com `"taskbar_embed": true` a barra é reparentada pra dentro do `Shell_TrayWnd`
— a janela real da taskbar — logo à esquerda da área de notificação.

Fica desligado por padrão de propósito: **não existe API suportada pra isso**. A
árvore de janelas do shell muda entre versões do Windows 11, e o Explorer
redesenha a taskbar em vários eventos (troca de DPI, "mostrar área de trabalho",
reinício do `explorer.exe`), levando a janela filha junto. Se o reparenting
falhar, a barra volta sozinha pro modo flutuante, que é o caminho confiável — e
é por isso que o modo flutuante é o padrão.

### Calibrar o fallback

O fallback conta tokens, não percentual — o percentual real é calculado no
servidor. Sem `fallback.token_budget` definido, a barra mostra o volume
(`~660k tok`) e o tempo restante da janela, em vez de inventar um número.

Pra transformar isso em percentual:

1. Use o Claude Code normalmente por um tempo.
2. Rode `/usage` dentro do Claude Code e anote o percentual da sessão.
3. Rode `python -m ctsbar --once` e anote os **tokens ponderados**.
4. `token_budget ≈ tokens_ponderados / (percentual / 100)`

Exemplo: 33 000 ponderados com `/usage` marcando 12% → orçamento ≈ 275 000.

Os pesos (`fallback.weights`) refletem o custo relativo de cada tipo de token —
output é caro, cache lido é barato:

```jsonc
"weights": { "input": 1.0, "output": 5.0, "cache_creation": 1.25, "cache_read": 0.1 }
```

Isso só importa quando a API está fora do ar. No uso normal a barra usa o
percentual exato e ignora tudo isso.

---

## Como está organizado

```
ctsbar/
├── models.py            tipos compartilhados, faixas de cor, formatação
├── paths.py             descoberta das pastas do Claude Code
├── config.py            config JSON com merge sobre os padrões
├── window.py            cálculo da janela rolante de 5 h  ← leitura ≠ cálculo
├── sources/
│   ├── api.py           API OAuth (exato)      ← leitura dos dados
│   └── transcripts.py   JSONL local (estimativa)
├── monitor.py           thread de polling, escolha de fonte, fallback
├── ui/
│   ├── bar.py           barra tkinter sempre no topo   ← interface visual
│   ├── tray.py          ícone da bandeja + menu
│   └── taskbar_embed.py reparenting experimental
├── autostart.py         chave Run do registro (HKCU)
├── winapi.py            envelopes ctypes (work area, DPI, estilos)
└── app.py               fiação dos três pedaços
```

As três camadas que o projeto separa — **leitura/parsing**, **cálculo da
janela** e **interface** — não se importam entre si: `sources/` e `window.py`
não sabem que o Windows existe, e `ui/` só conhece o `UsageSnapshot`.

Testes das camadas puras (rodam em qualquer sistema):

```powershell
pip install pytest
python -m pytest tests -q
```

---

## Problemas comuns

**"Sem credencial do Claude Code"** — o `.credentials.json` não existe ou não
tem `claudeAiOauth.accessToken`. Rode `claude` uma vez e faça login. Se você usa
`CLAUDE_CONFIG_DIR`, a barra respeita a variável.

**"Credencial expirada ou sem permissao"** — o token venceu. Abra o Claude Code:
ele renova sozinho e a barra volta ao normal no ciclo seguinte. A barra não
mexe nas suas credenciais.

**A barra some ao trocar de aplicativo** — era um bug, corrigido. Se ainda
acontecer, atualize (`git pull`) e reinicie. Uma janela sem borda no Windows
perde o "sempre no topo" quando outro processo assume o foreground; a barra
agora reafirma esse estado a cada 3 s.

**A barra some ao mostrar a área de trabalho** — sintoma do `taskbar_embed`.
Deixe em `false`.

**A barra não aparece** — pode estar escondida (`bar.visible: false`) ou fora da
tela depois de trocar de monitor. Menu da bandeja → **Mostrar barra** e
**Reposicionar barra**.

**Não aparece ícone na bandeja** — `pip install pystray Pillow`. Sem eles a barra
flutuante funciona, mas sem menu.

**`tkinter nao encontrado`** — reinstale o Python do python.org marcando
"tcl/tk and IDLE". Enquanto isso, `python -m ctsbar --watch` funciona.

**Fica sempre em `(fonte: transcripts)`** — a API não está respondendo; o motivo
aparece em `python -m ctsbar --once`. Normalmente é credencial ou proxy.

---

## Limites conhecidos

- O `/api/oauth/usage` é um endpoint interno do Claude Code, não uma API pública
  documentada. Funciona hoje (CLI 2.1.220) e pode mudar sem aviso — se mudar, a
  barra cai no fallback em vez de quebrar.
- O fallback estima; ele não tem como saber o percentual real, que é calculado
  no servidor.
- O embed na taskbar é experimental pelos motivos acima.
- Testado no Windows 11. O modo `--watch` roda em qualquer sistema.

## Referências

- [Manage costs effectively — Claude Code Docs](https://code.claude.com/docs/en/costs)
- [Use Claude Code with your Pro or Max plan](https://support.anthropic.com/en/articles/11145838-using-claude-code-with-your-pro-or-max-plan)
- [How do usage and length limits work?](https://support.anthropic.com/en/articles/11647753-understanding-usage-and-length-limits)
