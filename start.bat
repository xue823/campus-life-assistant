@echo off
chcp 65001 >nul
title Campus Life Assistant
cd /d "%~dp0"

set PORT=8866
set PY=

rem --- 1) prefer the bundled runtime (no need to install Python) ---
if exist "%~dp0runtime\python\python.exe" set PY=%~dp0runtime\python\python.exe

rem --- 2) otherwise look for a system Python ---
if "%PY%"=="" where python >nul 2>nul && set PY=python
if "%PY%"=="" where py >nul 2>nul && set PY=py

if "%PY%"=="" goto NOPY

echo ============================================================
echo   Campus Life Assistant  /  campus-life-assistant
echo   Schedule . Canteen . Expense . Campus Q and A
echo ------------------------------------------------------------
echo   Login: 20230101    Password: 123456
echo   Python: %PY%
echo   Open : http://127.0.0.1:%PORT%/
echo   Close this window to stop the server.
echo ============================================================
echo.

start "" http://127.0.0.1:%PORT%/
"%PY%" server.py --port %PORT%
pause
exit /b 0

:NOPY
echo.
echo [ERROR] Python was not found, and the bundled runtime is missing.
echo         Please unzip the whole package (keep the runtime folder),
echo         or install Python 3.8+ from https://www.python.org/downloads/
echo.
pause
exit /b 1
