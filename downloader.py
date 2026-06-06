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


def _try_tikwm(url: str, job_id: str) -> Optional[str]:
    """TikTok fallback через tikwm.com API.

    Не потребує cookies, тягне public TikTok без watermark.
    Повертає шлях до скачаного .mp4 або None при провалі.
    Логіка: tikwm віддає JSON з data.play (no watermark) → ми качаємо
    урла за тим лінком як звичайний HTTP файл.
    """
    import json
    import urllib.request

    out_dir = _downloads_dir()
    try:
        api_url = f"https://www.tikwm.com/api/?url={urllib.request.quote(url, safe=':/?&=')}"
        req = urllib.request.Request(
            api_url,
            headers={
                'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) '
                              'AppleWebKit/537.36 Chrome/120.0.0.0',
            },
        )
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = json.loads(resp.read().decode('utf-8'))
    except Exception as e:
        print(f"[download] tikwm API fail: {e}")
        return None

    if data.get('code') != 0:
        print(f"[download] tikwm rejected: {data.get('msg')}")
        return None

    info = data.get('data') or {}
    # Найвища якість: hdplay (1080p) → play (720p без watermark) → wmplay (з watermark)
    # Деякі відео не мають hdplay — для них play це і є максимальна якість.
    play_url = info.get('hdplay') or info.get('play') or info.get('wmplay')
    used_quality = ('hdplay' if info.get('hdplay')
                    else 'play' if info.get('play') else 'wmplay')
    print(f"[download] tikwm chose quality: {used_quality}")
    if not play_url:
        print("[download] tikwm: no play url in response")
        return None

    # tikwm повертає шляхи на свій CDN — додаємо https://
    if play_url.startswith('//'):
        play_url = 'https:' + play_url
    elif play_url.startswith('/'):
        play_url = 'https://www.tikwm.com' + play_url

    title = (info.get('title') or 'tiktok').strip()
    # Безпечне ім'я файлу — прибираємо спецсимволи
    safe_title = re.sub(r'[^\w\-.\s]', '_', title)[:80]
    out_path = out_dir / f"{job_id}_{safe_title}.mp4"

    try:
        _upd(job_id, status='downloading', progress=20,
             title=info.get('title', ''))
        req = urllib.request.Request(
            play_url,
            headers={'User-Agent': 'Mozilla/5.0'},
        )
        with urllib.request.urlopen(req, timeout=60) as resp:
            total = int(resp.headers.get('Content-Length', 0))
            downloaded = 0
            with open(out_path, 'wb') as f:
                while True:
                    chunk = resp.read(64 * 1024)
                    if not chunk:
                        break
                    f.write(chunk)
                    downloaded += len(chunk)
                    if total:
                        pct = int(20 + (downloaded * 70 / total))  # 20→90%
                        _upd(job_id, progress=pct, downloaded=downloaded, total=total)
        _upd(job_id, progress=95)
        return str(out_path)
    except Exception as e:
        print(f"[download] tikwm direct download failed: {e}")
        return None


def _run_download(job_id: str, url: str) -> None:
    try:
        platform = detect_platform(url)

        # TikTok-specific fallback: tikwm API (no cookies, no login).
        # Робимо спочатку — багато публічних TikTok тепер вимагає login для
        # yt-dlp прямого скачування. tikwm — third-party-API але стабільний.
        if platform == 'tiktok':
            _upd(job_id, status='downloading', progress=5)
            tikwm_result = _try_tikwm(url, job_id)
            if tikwm_result and Path(tikwm_result).exists():
                _upd(job_id, status='done', progress=100, file_path=tikwm_result)
                return
            # Якщо tikwm впав → continue до yt-dlp fallback нижче
            print("[download] tikwm failed, falling back to yt-dlp")

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
