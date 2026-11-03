@echo off
chcp 65001 >nul
cd /d "%~dp0"
title Monitor SEIN - Publicar copia para la nube
if not exist ".venv\Scripts\python.exe" (
    echo Primero ejecuta Iniciar.bat una vez para preparar el entorno.
    pause
    exit /b 1
)
".venv\Scripts\python.exe" -m monitor.publicar
if errorlevel 1 (
    pause
    exit /b 1
)
where git >nul 2>nul
if errorlevel 1 goto manual
if not exist ".git" goto manual
git add -A
git commit -m "Datos del IEOD al %date%"
git push
if errorlevel 1 goto manual
echo.
echo Listo. Streamlit Community Cloud toma la nueva copia sola en 1 a 2 minutos.
pause
exit /b 0
:manual
echo.
echo Copia exportada en la carpeta "publicado".
echo Abre GitHub Desktop: escribe un resumen abajo a la izquierda, pulsa "Commit to main" y luego "Push origin".
pause
