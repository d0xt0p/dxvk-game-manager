@echo off
cd /d "%~dp0"
py -3 -m venv .venv
if errorlevel 1 exit /b 1
.venv\Scripts\python.exe -m pip install PyInstaller==6.22.3
if errorlevel 1 exit /b 1
.venv\Scripts\python.exe -m PyInstaller --noconfirm --clean --onefile --windowed --name DXVK-Game-Manager-0.2 app.py
if errorlevel 1 exit /b 1
echo Ready: dist\DXVK-Game-Manager-0.2.exe
pause
