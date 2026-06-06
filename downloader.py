"""Завантаження відео з TikTok / Instagram / YouTube через yt-dlp.

Чому yt-dlp: НЕ потребує login/cookies для публічного контенту, працює
напряму без 3rd-party API, тягне найкращу якість, працює офлайн (без
зовнішніх сервісів — лише сам платформа).

Підтримуються:
  • TikTok       — без watermark, mp4
  • Instagram    — публічні reels / posts (вимагається public URL)
  • YouTube      — bestvideo+bestaudio, mp4

Use:
    job_id = start_download(url, on_progress)
    # ...polling job state via get_job(job_id)
"""
from __future__ import annotations

import os
import re
import threading
import time
import uuid
from pathlib import Path
from typing import Callable, Dict, Optional


# Тимчасова папка для завантажень. Кладемо в %LOCALAPPDATA%\SlengUniquifier\downloads
# щоб не засмічувати tempdir системи. Очищається через окрему cleanup-логіку.
def _downloads_dir() -> Path:
    base = os.environ.get('LOCALAPPDATA') or os.environ.get('APPDATA') or str(Path.home())
    d = Path(base) / 'SlengUniquifier' / 'downloads'
    d.mkdir(parents=True, exist_ok=True)
    return d


# In-memory job-store (як у server.py для uniquification).
# job_id → { status, progress, file_path, error, started_at }
_jobs: Dict[str, dict] = {}
_lock = threading.Lock()


def _upd(job_id: str, **kw) -> None:
    with _lock:
        if job_id in _jobs:
            _jobs[job_id].update(kw)


def get_job(job_id: str) -> Optional[dict]:
    with _lock:
        return dict(_jobs.get(job_id, {})) if job_id in _jobs else None


def detect_platform(url: str) -> str:
    """Визначаємо платформу за URL для UI/логів."""
    url_lower = url.lower()
    if 'tiktok.com' in url_lower or 'vm.tiktok' in url_lower:
        return 'tiktok'
    if 'instagram.com' in url_lower or 'instagr.am' in url_lower:
        return 'instagram'
    if 'youtube.com' in url_lower or 'youtu.be' in url_lower:
        return 'youtube'
    return 'unknown'


def _yt_dlp_options(out_template: str, on_progress: Callable[[dict], None]) -> dict:
    """Стандартні опції yt-dlp без cookies, у найвищій якості mp4."""
    return {
        'outtmpl': out_template,
        'quiet': True,
        'no_warnings': True,
        'noplaylist': True,
        # Найвища якість mp4 (відео + аудіо склеєне, до 1080p+).
        # 'best' fallback якщо немає mp4 (наприклад, TikTok інколи).
        'format': 'bv*+ba/best',
        'merge_output_format': 'mp4',
        # Прогрес через hook замість парсингу stdout.
        'progress_hooks': [on_progress],
        # Не лізти в браузерні куки (інакше yt-dlp шукає сесії у Chrome/Firefox).
        'cookiesfrombrowser': None,
        # Соц.мережі люблять блокувати ботів за UA, прикидаємось браузером.
        'http_headers': {
            'User-Agent': (
                'Mozilla/5.0 (Windows NT 10.0; Win64; x64) '
                'AppleWebKit/537.36 (KHTML, like Gecko) '
                'Chrome/120.0.0.0 Safari/537.36'
            ),
        },
        # Таймаут на з'єднання — щоб не висіти безкінечно.
        'socket_timeout': 30,
        # Retry на тимчасові помилки (5xx, network).
        'retries': 3,
    }


def start_download(url: str) -> str:
    """Запускає фонове завантаження. Повертає job_id."""
    job_id = uuid.uuid4().hex[:12]
    with _lock:
        _jobs[job_id] = {
            'status': 'queued',
            'progress': 0,
            'file_path': None,
            'error': None,
            'platform': detect_platform(url),
            'url': url,
            'started_at': time.time(),
        }

    thread = threading.Thread(target=_run_download, args=(job_id, url), daemon=True)
    thread.start()
    return job_id


def _run_download(job_id: str, url: str) -> None:
    try:
        # Імпорт yt_dlp всередині треда — щоб startup server.exe не блокувався
        # на ~500ms ініціалізації yt-dlp коли download не потрібен.
        import yt_dlp

        out_dir = _downloads_dir()
        # Унікальний префікс на job_id щоб паралельні job'и не конфліктували
        out_template = str(out_dir / f"{job_id}_%(title).100s.%(ext)s")

        def progress_hook(d):
            status = d.get('status')
            if status == 'downloading':
                total = d.get('total_bytes') or d.get('total_bytes_estimate') or 0
                downloaded = d.get('downloaded_bytes', 0)
                pct = int(downloaded * 100 / total) if total else 0
                _upd(job_id, status='downloading', progress=pct,
                     downloaded=downloaded, total=total)
            elif status == 'finished':
                # Файл скачаний, ще може йти merge (склейка audio+video)
                _upd(job_id, status='merging', progress=95)

        _upd(job_id, status='downloading', progress=1)

        opts = _yt_dlp_options(out_template, progress_hook)
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(url, download=True)
            # Реальний шлях до файлу (після merge може мати інший суфікс)
            file_path = ydl.prepare_filename(info)
            # Після merge_output_format=mp4 файл може мати .mp4 розширення
            # навіть якщо prepare_filename повертає інше — перевіримо.
            if not Path(file_path).exists():
                mp4_path = re.sub(r'\.[^.]+$', '.mp4', file_path)
                if Path(mp4_path).exists():
                    file_path = mp4_path

        _upd(job_id, status='done', progress=100, file_path=file_path,
             title=info.get('title', ''))

    except Exception as e:
        err = str(e)
        # Друк типу помилки у server.log для діагностики
        print(f"[download] job={job_id} failed: {type(e).__name__}: {err}")
        _upd(job_id, status='error', error=err)


def cleanup_old_downloads(max_age_hours: int = 24) -> int:
    """Видаляє файли старше max_age_hours. Викликається cron'ом server.py."""
    out_dir = _downloads_dir()
    cutoff = time.time() - max_age_hours * 3600
    deleted = 0
    for f in out_dir.iterdir():
        try:
            if f.is_file() and f.stat().st_mtime < cutoff:
                f.unlink()
                deleted += 1
        except OSError:
            pass
    return deleted
