@echo off
setlocal
REM One-click setup for the OpenOCD MCP server.
REM Creates the Python venv, installs dependencies, and registers the server
REM with Claude Code (user scope, so it's available in every project).
REM Run this once per machine. Safe to re-run.

set "DP=%~dp0"
set "VENV_PY=%DP%.venv\Scripts\python.exe"

echo ============================================
echo   OpenOCD MCP Server - setup
echo ============================================
echo.

REM --- 1) Python present? ---
where python >nul 2>nul
if errorlevel 1 (
  echo [ERROR] Python not found on PATH.
  echo         Install Python 3.10+ from https://www.python.org/downloads/
  echo         ^(tick "Add python.exe to PATH" in the installer^), then re-run setup.bat.
  pause & exit /b 1
)

REM --- 2) Create the virtual environment ---
if exist "%VENV_PY%" (
  echo [1/3] Virtual environment already exists - skipping.
) else (
  echo [1/3] Creating virtual environment...
  python -m venv "%DP%.venv"
  if errorlevel 1 ( echo [ERROR] Could not create venv. & pause & exit /b 1 )
)

REM --- 3) Install the package (editable) + its dependencies ---
echo [2/3] Installing openocd-mcp and dependencies...
"%VENV_PY%" -m pip install --quiet --upgrade pip
"%VENV_PY%" -m pip install --quiet -e "%DP%"
if errorlevel 1 ( echo [ERROR] pip install failed. & pause & exit /b 1 )

set "MCP_EXE=%DP%.venv\Scripts\openocd-mcp.exe"

REM --- 4) Register with Claude Code ---
where claude >nul 2>nul
if errorlevel 1 (
  echo [3/3] [WARN] 'claude' CLI not found on PATH - skipping registration.
  echo        After installing Claude Code, run this once:
  echo          claude mcp add --scope user openocd -- "%MCP_EXE%"
) else (
  echo [3/3] Registering 'openocd' with Claude Code ^(user scope^)...
  call claude mcp remove openocd >nul 2>nul
  call claude mcp add --scope user openocd -- "%MCP_EXE%"
)

echo.
echo ============================================
echo   Done!
echo ============================================
echo  1. Restart Claude Code so it loads the server.
echo  2. In your firmware project, tell Claude your chip
echo     ^(or add an openocd-mcp.json - see the README^).
echo  3. Plug in the board and say: "connect and halt the target".
echo.
pause
endlocal
