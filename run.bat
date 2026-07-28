@echo off
REM Sobe a barra em background, sem deixar janela de console aberta.
REM Usa o venv local se existir; senao, o pythonw do PATH.

setlocal
set "ROOT=%~dp0"

if exist "%ROOT%.venv\Scripts\pythonw.exe" (
    start "" "%ROOT%.venv\Scripts\pythonw.exe" "%ROOT%run.pyw"
) else (
    start "" pythonw "%ROOT%run.pyw"
)
endlocal
