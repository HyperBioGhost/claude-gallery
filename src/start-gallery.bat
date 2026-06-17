@echo off
cd /d "%LOCALAPPDATA%\ClaudeGallery"

:: Check if already running
curl.exe -s -o nul http://127.0.0.1:7477/ 2>nul
if %errorlevel%==0 (
    start "" "http://127.0.0.1:7477"
    exit
)

:: Start server hidden (no console window)
start "" /B "%LOCALAPPDATA%\ClaudeGallery\claude-gallery-server.exe" 2>"%TEMP%\gallery-error.log"
timeout /t 3 >nul

:: Verify it started
curl.exe -s -o nul http://127.0.0.1:7477/ 2>nul
if %errorlevel%==0 (
    start "" "http://127.0.0.1:7477"
    exit
)

:: If we get here, something failed
echo Gallery server failed to start.
echo.
type "%TEMP%\gallery-error.log" 2>nul
echo.
pause