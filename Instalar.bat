@echo off
setlocal
rem ===========================================================================
rem  Crea el entorno de Python de ChartScript. Se ejecuta UNA vez.
rem
rem  Si ya tienes el .venv del proyecto padre, esto no hace falta: los .bat lo
rem  encuentran solos. Sirve para cuando ChartScript se copia a otra maquina.
rem ===========================================================================

set "RAIZ=%~dp0"

if exist "%RAIZ%..\.venv\Scripts\python.exe" (
    echo Ya existe el entorno del proyecto padre:
    echo   %RAIZ%..\.venv
    echo Los .bat lo usaran solos, no hace falta instalar nada.
    echo.
    echo Si quieres uno propio para ChartScript de todas formas, borra esta
    echo comprobacion o crea el venv a mano.
    echo.
    pause
    exit /b 0
)

rem ffmpeg no se instala con pip: es un ejecutable del sistema y el pipeline lo
rem llama por linea de comandos para convertir, padear y normalizar el audio.
where ffmpeg >nul 2>nul
if errorlevel 1 (
    echo AVISO: no encuentro ffmpeg en el PATH.
    echo.
    echo Sin ffmpeg no se puede generar song.ogg ni medir el audio. Instalalo
    echo con:   winget install Gyan.FFmpeg
    echo y abre una consola nueva despues.
    echo.
    echo La instalacion de Python sigue igual; ffmpeg lo puedes poner despues.
    echo.
    pause
)

echo Creando el entorno en %RAIZ%.venv ...
py -3.11 -m venv "%RAIZ%.venv"
if errorlevel 1 (
    echo.
    echo No se pudo con py -3.11. Se prueba con el Python del PATH.
    python -m venv "%RAIZ%.venv"
    if errorlevel 1 (
        echo.
        echo ERROR: no hay un Python 3.11 o 3.12 usable.
        echo Instalalo desde python.org y vuelve a ejecutar este archivo.
        echo.
        pause
        exit /b 1
    )
)

echo.
echo Instalando dependencias...
"%RAIZ%.venv\Scripts\python.exe" -m pip install --upgrade pip
"%RAIZ%.venv\Scripts\python.exe" -m pip install -r "%RAIZ%requirements.txt"
if errorlevel 1 (
    echo.
    echo ERROR: fallo la instalacion de dependencias.
    echo.
    pause
    exit /b 1
)

echo.
echo Listo. Ya puedes arrastrar un MIDI a "Hacer chart.bat".
echo.
pause
exit /b 0
