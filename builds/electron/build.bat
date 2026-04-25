@echo off
setlocal
cd /d "%~dp0..\.."

echo ============================================
echo   Sleng Uniquifier - Electron Build
echo ============================================
echo.

:: ── 0. Generate icon ──────────────────────────────────────────────────────────
echo [0/4] Generating icon...
python -c "import PIL" >nul 2>&1
if errorlevel 1 pip install pillow >nul
python builds\electron\generate_icon.py

:: ── 1. Build server.exe ────────────────────────────────────────────────────────
echo [1/4] Building server.exe...
if not exist "builds\electron\resources" mkdir "builds\electron\resources"
if exist "builds\electron\resources\server.exe" del /f /q "builds\electron\resources\server.exe"

python -m PyInstaller builds\electron\server_entry.spec ^
    --distpath builds\electron\resources_tmp ^
    --workpath build_server ^
    --noconfirm
if errorlevel 1 (
    echo [ERROR] PyInstaller failed.
    pause & exit /b 1
)
copy /y "builds\electron\resources_tmp\server.exe" "builds\electron\resources\server.exe" >nul
rmdir /s /q "builds\electron\resources_tmp" >nul 2>&1
rmdir /s /q "build_server" >nul 2>&1

:: Copy ffmpeg
if exist "builds\windows\ffmpeg\ffmpeg.exe" (
    copy /y "builds\windows\ffmpeg\ffmpeg.exe"  "builds\electron\resources\" >nul
    copy /y "builds\windows\ffmpeg\ffprobe.exe" "builds\electron\resources\" >nul
    echo [INFO] FFmpeg copied.
) else (
    echo [WARN] ffmpeg.exe not found in builds\windows\ffmpeg\
)

:: ── 2. npm install ─────────────────────────────────────────────────────────────
echo [2/4] npm install...
cd builds\electron
call npm install
if errorlevel 1 ( echo [ERROR] npm install failed. & cd ..\.. & pause & exit /b 1 )

:: ── 3. electron-packager ───────────────────────────────────────────────────────
echo [3/4] Packaging with electron-packager...

:: Copy resources into Electron folder so they get bundled
if not exist "app_resources" mkdir "app_resources"
copy /y "resources\server.exe"  "app_resources\" >nul 2>&1
copy /y "resources\ffmpeg.exe"  "app_resources\" >nul 2>&1
copy /y "resources\ffprobe.exe" "app_resources\" >nul 2>&1

call npm run pack
if errorlevel 1 ( echo [ERROR] packager failed. & cd ..\.. & pause & exit /b 1 )

:: Move server.exe/ffmpeg into the packed app folder
set PACKED=..\..\dist\electron-packed\Sleng Uniquifier-win32-x64
copy /y "resources\server.exe"  "%PACKED%\resources\" >nul 2>&1
copy /y "resources\ffmpeg.exe"  "%PACKED%\resources\" >nul 2>&1
copy /y "resources\ffprobe.exe" "%PACKED%\resources\" >nul 2>&1

cd ..\..

:: ── 4. Inno Setup installer ────────────────────────────────────────────────────
echo [4/4] Creating installer...
set ISCC="%ProgramFiles(x86)%\Inno Setup 6\ISCC.exe"
if not exist %ISCC% set ISCC="%ProgramFiles%\Inno Setup 6\ISCC.exe"

if exist %ISCC% (
    if not exist "dist\installer" mkdir "dist\installer"
    %ISCC% builds\electron\setup_electron.iss
    echo.
    echo [OK] dist\installer\SlengUniquifier_Setup_v1.0.0.exe
) else (
    echo [WARN] Inno Setup not found. App is at:
    echo        dist\electron-packed\Sleng Uniquifier-win32-x64\
)

echo.
echo ============================================
echo   Done!
echo ============================================
pause
