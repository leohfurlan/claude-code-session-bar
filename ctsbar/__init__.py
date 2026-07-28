"""Barra de uso da sessao do Claude Code para Windows 11.

Camadas:
  sources/  leitura dos dados (API OAuth + transcripts JSONL)
  window.py calculo da janela rolante de 5h
  monitor.py orquestracao e polling
  ui/       barra flutuante, icone de bandeja e embed experimental
"""

__version__ = "1.0.0"
