@echo off
setlocal
cd /d "%~dp0"

if not exist "dist\SmartAttendance.exe" (
    echo [ERROR] dist\SmartAttendance.exe was not found.
    echo Run build.bat first, then run this script again.
    pause
    exit /b 1
)

if not exist "installer\SmartAttendance.iss" (
    echo [ERROR] installer\SmartAttendance.iss was not found.
    pause
    exit /b 1
)

set "ISCC=%ProgramFiles(x86)%\Inno Setup 6\ISCC.exe"
if not exist "%ISCC%" set "ISCC=%ProgramFiles%\Inno Setup 6\ISCC.exe"
if not exist "%ISCC%" set "ISCC=%LOCALAPPDATA%\Programs\Inno Setup 6\ISCC.exe"
if not exist "%ISCC%" (
    echo [ERROR] Inno Setup 6 was not found.
    echo Install it from https://jrsoftware.org/isdl.php and try again.
    pause
    exit /b 1
)

echo [1/1] Compiling the distribution setup...
"%ISCC%" "installer\SmartAttendance.iss"
if errorlevel 1 goto :error

if not exist "dist\SmartAttendance_Setup_1.1.7.exe" (
    echo [ERROR] The setup executable was not produced.
    goto :error
)

echo.
echo Build complete: dist\SmartAttendance_Setup_1.1.7.exe
exit /b 0

:error
echo.
echo Setup build failed. Review the messages above.
pause
exit /b 1
