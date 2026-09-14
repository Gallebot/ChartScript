@echo off
setlocal enabledelayedexpansion
rem ===========================================================================
rem  ChartScript - arrastra uno o varios MIDI encima de este archivo.
rem
rem  Te muestra las pistas del MIDI, te deja elegir que pista va a que canal del
rem  chart, y deja cada resultado en ChartScript\out\ listo para copiar a la
rem  carpeta de canciones de Clone Hero.
rem
rem  Si al lado del MIDI hay un audio con el MISMO nombre, se usa:
rem      Artista - Titulo.mid
rem      Artista - Titulo.mp3   <- el tempo se ajusta a esta grabacion
rem  Sin audio tambien funciona, pero el chart sale con song.ogg en silencio:
rem  editable en Moonscraper, no jugable con musica.
rem ===========================================================================

rem --- Configuracion --------------------------------------------------------
rem  FORMATO: chart, mid o both.
rem   - chart  Clone Hero lo juega Y Moonscraper lo edita. Es la opcion sensata.
rem   - both   escribe los dos, pero OJO: con ambos presentes Clone Hero se
rem            queda con el .mid e ignora el .chart, asi que editar el .chart
rem            no cambiaria nada de lo que suena.
set "FORMATO=chart"
rem  SECCIONES=1 detecta Intro/Verse/Chorus del audio (necesita audio).
set "SECCIONES=0"
rem  REEMPLAZAR=1 permite pisar una carpeta que ya exista en out\.
set "REEMPLAZAR=0"
rem --------------------------------------------------------------------------

set "RAIZ=%~dp0"
set "SALIDA=%RAIZ%out"

rem --- Buscar un Python usable, por orden de preferencia --------------------
set "PYTHON="
if exist "%RAIZ%.venv\Scripts\python.exe" set "PYTHON=%RAIZ%.venv\Scripts\python.exe"
if not defined PYTHON if exist "%RAIZ%..\.venv\Scripts\python.exe" set "PYTHON=%RAIZ%..\.venv\Scripts\python.exe"
if not defined PYTHON (
    echo ERROR: no encuentro un entorno de Python con las dependencias.
    echo.
    echo Se busco en:
    echo   %RAIZ%.venv\Scripts\python.exe
    echo   %RAIZ%..\.venv\Scripts\python.exe
    echo.
    echo Ejecuta "Instalar.bat" una vez y vuelve a intentarlo.
    echo.
    pause
    exit /b 1
)

if not exist "%SALIDA%" mkdir "%SALIDA%"

set "EXTRA="
if "%REEMPLAZAR%"=="1" set "EXTRA=%EXTRA% --force"
if "%SECCIONES%"=="1" set "EXTRA=%EXTRA% --sections"

rem Sin argumentos: doble clic, asi que se pregunta la ruta.
if "%~1"=="" (
    echo Arrastra un MIDI encima de este archivo, o escribe su ruta ahora.
    echo.
    set /p "ENTRADA=Ruta del MIDI: "
    if "!ENTRADA!"=="" exit /b 0
    "%PYTHON%" "%RAIZ%hacer_chart.py" "!ENTRADA:"=!" --out "%SALIDA%" --format "%FORMATO%"%EXTRA%
    echo.
    pause
    exit /b 0
)

rem Con argumentos: uno o varios MIDI soltados encima. Se pasan todos de una
rem vez, para que la TUI los recorra sin perder el estado entre archivos.
set "LISTA="
for %%F in (%*) do set "LISTA=!LISTA! "%%~fF""

"%PYTHON%" "%RAIZ%hacer_chart.py"%LISTA% --out "%SALIDA%" --format "%FORMATO%"%EXTRA%
set "CODIGO=%ERRORLEVEL%"

echo.
if not "%CODIGO%"=="0" (
    echo Termino con avisos o sin generar nada. Mira los mensajes de arriba.
) else (
    echo Listo. Las carpetas estan en:
    echo   %SALIDA%
    echo.
    echo Copialas a la carpeta de canciones de Clone Hero y escanea.
    echo Para editar un chart, abre su notes.chart en Moonscraper.
)
echo.
pause
exit /b %CODIGO%
