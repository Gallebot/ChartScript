@echo off
setlocal enabledelayedexpansion
rem ===========================================================================
rem  Comprueba que la cadena tick -> segundos -> archivo sigue siendo correcta.
rem
rem  Genera un chart de calibracion (una nota por negra sobre un metronomo) y lo
rem  mide: compara los onsets del audio con los ticks del .chart, parseando el
rem  .chart desde cero sin usar el propio TempoMap.
rem
rem  Si esto falla, el problema esta en el pipeline y no en el MIDI que estes
rem  intentando convertir. Es lo primero que hay que correr cuando algo "suena
rem  corrido" y no se sabe de quien es la culpa.
rem
rem  Doble clic para la prueba estandar. Tambien se le puede arrastrar una
rem  carpeta de cancion ya generada para medir esa en vez de una nueva.
rem ===========================================================================

set "RAIZ=%~dp0"

set "PYTHON="
if exist "%RAIZ%.venv\Scripts\python.exe" set "PYTHON=%RAIZ%.venv\Scripts\python.exe"
if not defined PYTHON if exist "%RAIZ%..\.venv\Scripts\python.exe" set "PYTHON=%RAIZ%..\.venv\Scripts\python.exe"
if not defined PYTHON (
    echo ERROR: no encuentro el entorno de Python. Ejecuta "Instalar.bat".
    echo.
    pause
    exit /b 1
)

where ffmpeg >nul 2>nul
if errorlevel 1 (
    echo ERROR: no encuentro ffmpeg en el PATH.
    echo La verificacion necesita ffmpeg para detectar los clicks del audio.
    echo   winget install Gyan.FFmpeg
    echo.
    pause
    exit /b 1
)

rem Con una carpeta arrastrada: se mide esa y no se genera nada nuevo.
if not "%~1"=="" (
    if exist "%~1\notes.chart" (
        echo Midiendo %~nx1 ...
        echo.
        "%PYTHON%" "%RAIZ%tools\verify_timing.py" "%~f1"
        echo.
        pause
        exit /b 0
    )
    echo ERROR: %~nx1 no parece una carpeta de cancion: no tiene notes.chart.
    echo.
    pause
    exit /b 1
)

rem --- Prueba estandar: dos BPM, uno redondo y uno que no ------------------
rem  174.267 no es capricho: el evento B del .chart guarda BPM*1000 como entero,
rem  asi que un BPM con mas decimales es justo el que delata una deriva entre el
rem  mapa en memoria y el que calcula el juego.
rem
rem  calibrar.py --verify genera y mide en el mismo proceso. Antes esto parseaba
rem  la ruta de la carpeta de la salida del generador, y no funcionaba: con
rem  delims=: el "C:" de la unidad ya partia la ruta en dos.
for %%V in (120 174.267) do (
    echo.
    echo ==========================================================
    echo  Calibracion a %%V BPM
    echo ==========================================================
    "%PYTHON%" "%RAIZ%calibrar.py" --bpm %%V --out "%RAIZ%out" --verify --format chart
    if errorlevel 1 echo   ^(fallo: mira el mensaje de arriba^)
)

echo.
echo Lo que importa no es el error absoluto (Vorbis mete un sesgo de ~2 ms):
echo es la DERIVA. Un error de BPM crece a lo largo de la cancion, un
echo artefacto del codec no.
echo.
pause
exit /b 0
