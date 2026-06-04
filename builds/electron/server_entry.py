#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Entry point for PyInstaller → server.exe
Electron spawns this as a subprocess.

ВАЖЛИВО: stdout/stderr перенаправляємо у log-файл, щоб у разі краху
ми могли побачити причину (Electron не показує console у packed mode).
Без цього будь-який ImportError або налаштування antivirus вбиває
сервер «мовчки», а юзер бачить тільки «Сервер не доступний».
"""
import io
import os
import sys
import traceback
import webbrowser
from datetime import datetime
from pathlib import Path


# ─────────────────────────────────────────────────────────────────────────────
# 0. Підготовка лог-папки В САМОМУ ПОЧАТКУ — щоб ловити навіть startup-крах
# ─────────────────────────────────────────────────────────────────────────────

def _log_dir() -> Path:
    base = os.environ.get('LOCALAPPDATA') or os.environ.get('APPDATA') or str(Path.home())
    d = Path(base) / 'SlengUniquifier'
    try:
        d.mkdir(parents=True, exist_ok=True)
    except Exception:
        # У дуже рідких випадках LOCALAPPDATA може бути недоступний.
        # Fallback у tempdir щоб хоч щось писалось.
        import tempfile
        d = Path(tempfile.gettempdir()) / 'SlengUniquifier'
        d.mkdir(parents=True, exist_ok=True)
    return d


LOG_DIR = _log_dir()
LOG_FILE = LOG_DIR / 'server.log'


def _setup_logging() -> None:
    """Перенаправити stdout/stderr у LOG_FILE. Тримаємо лише останній мегабайт."""
    try:
        # Якщо файл великий — обрізаємо щоб не накопичувати
        if LOG_FILE.exists() and LOG_FILE.stat().st_size > 1_000_000:
            try:
                LOG_FILE.unlink()
            except OSError:
                pass

        # Відкриваємо у append + utf-8
        f = open(LOG_FILE, 'a', encoding='utf-8', errors='replace', buffering=1)
        f.write(f"\n\n{'=' * 60}\n[{datetime.now().isoformat()}] server starting\n{'=' * 60}\n")
        f.write(f"sys.executable: {sys.executable}\n")
        f.write(f"sys.frozen: {getattr(sys, 'frozen', False)}\n")
        f.write(f"sys.platform: {sys.platform}\n")
        f.write(f"_MEIPASS: {getattr(sys, '_MEIPASS', '—')}\n")
        f.write(f"cwd: {os.getcwd()}\n\n")
        f.flush()

        sys.stdout = f
        sys.stderr = f
    except Exception:
        # Якщо лог не вдалось — продовжуємо тихо
        try:
            sys.stdout = open(os.devnull, 'w', encoding='utf-8')
            sys.stderr = open(os.devnull, 'w', encoding='utf-8')
        except Exception:
            pass


_setup_logging()
os.environ['PYTHONIOENCODING'] = 'utf-8'


# ─────────────────────────────────────────────────────────────────────────────
# 1. Резолвимо шляхи
# ─────────────────────────────────────────────────────────────────────────────

try:
    if getattr(sys, 'frozen', False):
        base = Path(sys._MEIPASS)
        exe_dir = Path(sys.executable).parent
    else:
        base = Path(__file__).resolve().parent.parent.parent
        exe_dir = base

    # Додаємо exe_dir у PATH щоб ffmpeg.exe (поруч з server.exe) знайшовся
    os.environ['PATH'] = str(exe_dir) + os.pathsep + os.environ.get('PATH', '')
    sys.path.insert(0, str(base))

    print(f"[INFO] base={base}")
    print(f"[INFO] exe_dir={exe_dir}")
    print(f"[INFO] PATH prefix added: {exe_dir}")

    # Перевіряємо що ffmpeg/ffprobe реально присутні
    for tool in ('ffmpeg.exe', 'ffprobe.exe'):
        p = exe_dir / tool
        if p.exists():
            print(f"[OK] {tool} → {p}")
        else:
            print(f"[WARN] {tool} not found at {p}")

except Exception as e:
    print(f"[FATAL] path setup failed: {e}")
    traceback.print_exc()
    sys.exit(1)


# ─────────────────────────────────────────────────────────────────────────────
# 2. Імпорт server і запуск
# ─────────────────────────────────────────────────────────────────────────────

try:
    print("[INFO] importing server module...")
    import server as srv

    srv.BASE_DIR = base
    webbrowser.open = lambda *a, **kw: None  # Electron сам відкриває вікно

    print("[INFO] starting srv.main()...")
    srv.main()

except SystemExit as e:
    print(f"[INFO] SystemExit: code={e.code}")
    raise
except Exception as e:
    print(f"[FATAL] startup error: {e}")
    traceback.print_exc()
    # Залишаємо процес мертвим — Electron побачить що порт не відкритий
    sys.exit(2)
