@echo off
rem ==========================================================================
rem  Erstellt dist\VermietFuchs\VermietFuchs.exe (Ordner mit
rem  Bibliotheken) und daraus eine ZIP-Datei zum Weitergeben.
rem  Voraussetzung: virtuelles Environment .venv mit requirements-dev.txt
rem  (siehe README.md, Abschnitt "Einrichtung").
rem ==========================================================================
setlocal
cd /d "%~dp0"

if not exist ".venv\Scripts\python.exe" (
    echo FEHLER: .venv nicht gefunden. Bitte zuerst die Einrichtung aus README.md ausfuehren.
    exit /b 1
)

rem --onedir     : .exe samt Bibliotheken in einen Ordner (startet schneller als eine
rem                Einzeldatei und wird seltener faelschlich vom Virenscanner gemeldet)
rem --windowed   : kein schwarzes Konsolenfenster beim Start
rem --icon       : Symbol der .exe im Explorer und in der Taskleiste
rem --add-data   : Stylesheet, Anleitung und Grafiken mitliefern (Quelle;Zielordner)
rem --exclude-module : Entwicklerwerkzeuge und Tests gehoeren nicht in die .exe
".venv\Scripts\python.exe" -m PyInstaller ^
    --noconfirm ^
    --clean ^
    --onedir ^
    --windowed ^
    --name VermietFuchs ^
    --icon "ui\resources\app_icon.ico" ^
    --add-data "ui\resources\app_icon.png;ui\resources" ^
    --add-data "ui\resources\logo.png;ui\resources" ^
    --add-data "ui\styles\stylesheet.qss;ui\styles" ^
    --add-data "ui\resources\belegerkennung.md;ui\resources" ^
    --collect-all winrt ^
    --exclude-module tools ^
    --exclude-module tests ^
    --exclude-module pytest ^
    main.py

if errorlevel 1 (
    echo.
    echo FEHLER: Der Build ist fehlgeschlagen.
    exit /b 1
)

rem Versionsnummer aus config.py lesen und den Ordner als ZIP verpacken.
for /f "usebackq delims=" %%v in (`.venv\Scripts\python.exe -c "import config; print(config.APP_VERSION)"`) do set APP_VERSION=%%v
set ZIP_NAME=dist\VermietFuchs-%APP_VERSION%-windows.zip
if exist "%ZIP_NAME%" del "%ZIP_NAME%"
copy /y LICENSE dist\VermietFuchs\LICENSE.txt >nul
powershell -NoProfile -Command "Compress-Archive -Path 'dist\VermietFuchs' -DestinationPath '%ZIP_NAME%'"
if errorlevel 1 (
    echo.
    echo FEHLER: Die ZIP-Datei konnte nicht erstellt werden.
    exit /b 1
)

echo.
echo Fertig: dist\VermietFuchs\VermietFuchs.exe
echo         %ZIP_NAME%
echo Die config.json und der Ordner "logs" entstehen beim ersten Start neben der .exe.
endlocal
