@echo off
title Video Uniquifier

python --version >nul 2>&1
if errorlevel 1 (
    echo Python not found. Install from https://python.org
    pause
    exit /b 1
)

python "%~dp0server.py"

echo.
echo Сервер зупинено. Код виходу: %errorlevel%
if exist "%~dp0server_error.log" (
    echo.
    echo === ЛОГ ПОМИЛКИ ===
    type "%~dp0server_error.log"
)
pause
