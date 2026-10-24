@echo off
setlocal
cd /d "%~dp0"

if not exist ".venv\Scripts\python.exe" (
    echo [1/4] Creating the Python virtual environment...
    py -3 -m venv .venv
    if errorlevel 1 goto :error
)

echo [2/4] Installing build dependencies...
".venv\Scripts\python.exe" -m pip install --upgrade pip
".venv\Scripts\python.exe" -m pip install -r requirements-dev.txt
if errorlevel 1 goto :error

echo [3/4] Generating the application icon...
".venv\Scripts\python.exe" scripts\make_icon.py
if errorlevel 1 goto :error

echo [4/4] Building dist\SmartAttendance.exe...
".venv\Scripts\python.exe" -m PyInstaller --noconfirm --clean attendance.spec
if errorlevel 1 goto :error

echo.
echo Build complete: dist\SmartAttendance.exe
exit /b 0

:error
echo.
echo Build failed. Review the messages above.
pause
exit /b 1
