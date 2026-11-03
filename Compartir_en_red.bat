@echo off
chcp 65001 >nul
cd /d "%~dp0"
title Monitor SEIN - compartido en la red
if not exist ".venv\Scripts\python.exe" (
    echo Primero ejecuta Iniciar.bat una vez para preparar el entorno.
    pause
    exit /b 1
)
echo.
echo  ============================================================
echo   Comparte este link con tus companeros (misma red o VPN):
echo.
for /f "usebackq delims=" %%i in (`powershell -NoProfile -Command "Get-NetIPAddress -AddressFamily IPv4 ^| Where-Object { $_.IPAddress -notlike '127.*' -and $_.IPAddress -notlike '169.254.*' } ^| Select-Object -ExpandProperty IPAddress"`) do echo      http://%%i:8501
echo.
echo   Si Windows pregunta por el firewall, permite "Redes privadas".
echo   Deja esta ventana abierta mientras otros lo usan.
echo  ============================================================
echo.
".venv\Scripts\python.exe" -m streamlit run app.py --server.address 0.0.0.0 --server.port 8501 --server.headless false
pause
