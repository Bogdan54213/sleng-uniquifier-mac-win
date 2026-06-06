# -*- mode: python ; coding: utf-8 -*-
# PyInstaller spec: builds server.exe (one-file, no console)
# Run from project root: python -m PyInstaller builds/electron/server_entry.spec

from pathlib import Path

ROOT = Path('.').resolve()

a = Analysis(
    [str(ROOT / 'builds/electron/server_entry.py')],
    pathex=[str(ROOT)],
    binaries=[],
    datas=[
        (str(ROOT / 'index.html'),    '.'),
        (str(ROOT / 'server.py'),     '.'),
        (str(ROOT / 'uniquifier.py'), '.'),
        (str(ROOT / 'auth.py'),       '.'),
        (str(ROOT / 'bot_notify.py'), '.'),
        (str(ROOT / 'config.py'),     '.'),
        (str(ROOT / 'downloader.py'), '.'),
        # Шрифти бренд-стилю — Orbitron (logo) + CascadiaCode (mono)
        (str(ROOT / 'fonts'),         'fonts'),
        (str(ROOT / 'assets'),        'assets'),
    ],
    hiddenimports=[
        'server',
        'uniquifier',
        'auth',
        'bot_notify',
        'config',
        'downloader',
        'http.server',
        'socketserver',
        'webbrowser',
        'uuid',
        'subprocess',
        'sqlite3',
        'hmac',
        'hashlib',
        'urllib.request',
        # yt-dlp — для завантаження TikTok / Instagram / YouTube без cookies
        'yt_dlp',
        'yt_dlp.extractor',
        'yt_dlp.extractor.lazy_extractors',
    ],
    hookspath=[],
    runtime_hooks=[],
    excludes=['tkinter', 'unittest', 'test', 'PyQt5', 'PyQt6', 'webview'],
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    name='server',
    debug=False,
    strip=False,
    upx=False,
    console=False,      # no black terminal window
    onefile=True,
)
