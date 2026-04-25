#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Entry point for PyInstaller → server.exe
Electron spawns this as a subprocess.
"""
import os
import sys
import io
import webbrowser
from pathlib import Path

# Force UTF-8 stdout/stderr so emoji in print() don't crash on Windows CP1251
try:
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8', errors='replace')
except AttributeError:
    sys.stdout = open(os.devnull, 'w', encoding='utf-8')
    sys.stderr = open(os.devnull, 'w', encoding='utf-8')
os.environ['PYTHONIOENCODING'] = 'utf-8'

# ── Resolve base directory ────────────────────────────────────────────────────
if getattr(sys, 'frozen', False):
    # Frozen: files extracted to _MEIPASS, exe is in resources/
    base     = Path(sys._MEIPASS)
    exe_dir  = Path(sys.executable).parent
else:
    base     = Path(__file__).resolve().parent.parent.parent  # project root
    exe_dir  = base

# Add exe_dir to PATH so ffmpeg.exe (placed next to server.exe) is found
os.environ['PATH'] = str(exe_dir) + os.pathsep + os.environ.get('PATH', '')

# Add base to sys.path so server.py can import uniquifier.py
sys.path.insert(0, str(base))

# ── Patch & start server ─────────────────────────────────────────────────────
import server as srv

srv.BASE_DIR = base                                   # index.html location
webbrowser.open = lambda *a, **kw: None               # Electron opens the window

srv.main()
