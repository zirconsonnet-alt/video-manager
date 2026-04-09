@echo off
setlocal
cd /d "%~dp0"

if exist "%~dp0start_video_manager_silent.vbs" (
    start "" wscript.exe //nologo "%~dp0start_video_manager_silent.vbs"
    exit /b 0
)

echo [Error] start_video_manager_silent.vbs not found.
echo Please keep the launcher files in the same folder.
pause
exit /b 1
