@echo off
rem Startet die Anwendung im Entwicklungsmodus (ohne .exe-Build).
cd /d "%~dp0"
if not exist ".venv\Scripts\pythonw.exe" (
    echo FEHLER: .venv nicht gefunden. Bitte zuerst die Einrichtung aus README.md ausfuehren.
    pause
    exit /b 1
)
rem pythonw statt python: kein schwarzes Konsolenfenster. Fehler stehen in logs\.
start "" ".venv\Scripts\pythonw.exe" main.py
