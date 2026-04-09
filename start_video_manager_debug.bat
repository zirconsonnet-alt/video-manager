@echo off
setlocal
chcp 65001 >nul
cd /d "%~dp0"

set "PYTHON_CMD="
set "PYTHONW_CMD="

where py >nul 2>nul
if %errorlevel%==0 (
    set "PYTHON_CMD=py -3"
    set "PYTHONW_CMD=pyw -3"
) else (
    where python >nul 2>nul
    if %errorlevel%==0 (
        set "PYTHON_CMD=python"
    )
    where pythonw >nul 2>nul
    if %errorlevel%==0 (
        set "PYTHONW_CMD=pythonw"
    )
)

if not defined PYTHON_CMD (
    echo [Error] Python 3 not found.
    echo Please install Python 3 and add it to PATH.
    pause
    exit /b 1
)

echo [1/3] Checking dependencies...
%PYTHON_CMD% -c "import bilibili_api, aiohttp, tkinter, ttkbootstrap" >nul 2>nul
if errorlevel 1 (
    echo [2/3] Installing dependencies...
    %PYTHON_CMD% -m pip install -r requirements.txt
    if errorlevel 1 (
        echo [Error] Failed to install dependencies.
        pause
        exit /b 1
    )
)

echo [3/3] Starting Video Manager...
if defined PYTHONW_CMD (
    start "" %PYTHONW_CMD% start_video_manager.pyw
) else (
    start "" %PYTHON_CMD% start_video_manager.pyw
)

endlocal
