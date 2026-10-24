@echo off
setlocal
cd /d "%~dp0"

if not exist ".venv\Scripts\python.exe" (
    echo [1/3] Creating the Python virtual environment...
    py -3 -m venv .venv
    if errorlevel 1 goto :error
    echo [2/3] Installing dependencies...
    ".venv\Scripts\python.exe" -m pip install --upgrade pip
    ".venv\Scripts\python.exe" -m pip install -r requirements.txt
    if errorlevel 1 goto :error
)

echo [3/3] Starting SmartAttendance...
".venv\Scripts\python.exe" app.py %*
if errorlevel 1 pause
exit /b 0

:error
echo.
echo Failed to prepare the environment. Make sure Python 3.11+ is installed and available through the "py" launcher.
pause
exit /b 1
