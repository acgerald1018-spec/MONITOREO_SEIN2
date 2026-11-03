@echo off
chcp 65001 >nul
cd /d "%~dp0"
title Monitor SEIN

if not exist ".venv\Scripts\python.exe" (
    echo Preparando el entorno por primera vez ^(unos minutos^)...
    where py >nul 2>nul && (py -3 -m venv .venv) || (python -m venv .venv)
    if not exist ".venv\Scripts\python.exe" (
        echo No encontre Python. Instala Python 3.11 o mas nuevo desde python.org y marca "Add to PATH".
        pause
        exit /b 1
    )
    ".venv\Scripts\python.exe" -m pip install --upgrade pip
    ".venv\Scripts\python.exe" -m pip install -r requirements.txt
)

echo Abriendo el cuadro de control en el navegador...
".venv\Scripts\python.exe" -m streamlit run app.py --server.port 8501 --server.headless false
pause
