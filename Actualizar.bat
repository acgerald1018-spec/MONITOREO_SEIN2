@echo off
chcp 65001 >nul
cd /d "%~dp0"
title Monitor SEIN - Actualizar
if not exist ".venv\Scripts\python.exe" (
    echo Primero ejecuta Iniciar.bat una vez para preparar el entorno.
    pause
    exit /b 1
)
".venv\Scripts\python.exe" actualizar.py %*
if "%~1"=="" pause
