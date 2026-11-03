@echo off
chcp 65001 >nul
cd /d "%~dp0"
rem Crea una tarea de Windows que actualiza la base cada 30 minutos (sin abrir ventanas).
rem Para quitarla:  schtasks /Delete /TN "MonitorSEIN" /F
schtasks /Create /SC MINUTE /MO 30 /TN "MonitorSEIN" /TR "\"%~dp0.venv\Scripts\pythonw.exe\" \"%~dp0actualizar.py\" --silencioso" /F
if errorlevel 1 (echo No se pudo crear la tarea.) else (echo Listo: la base se actualizara cada 30 minutos mientras la PC este encendida.)
pause
