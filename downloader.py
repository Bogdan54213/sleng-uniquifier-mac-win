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


def _user_cookies_file() -> Optional[Path]:
    """Шлях до користувацького cookies.txt якщо він є.
    %LOCALAPPDATA%\\SlengUniquifier\\cookies.txt — куди UI завантажує файл.
    """
    base = os.environ.get('LOCALAPPDATA') or os.environ.get('APPDATA') or str(Path.home())
    p = Path(base) / 'SlengUniquifier' / 'cookies.txt'
    return p if p.exists() and p.stat().st_size > 0 else None


def _yt_dlp_options(out_template: str, on_progress: Callable[[dict], None]) -> dict:
    """Опції yt-dlp. Якщо юзер залив свої cookies.txt — використовуємо їх.

    Cookies дозволяють YouTube/Instagram обходити anti-bot перевірки.
    Юзер експортує через browser-extension 'Get cookies.txt LOCALLY' →
    через UI заливає у Sleng → ми передаємо файл yt-dlp через 'cookiefile'.

    format='best' — найвища доступна якість.
    """
    opts = {
        'outtmpl': out_template,
        'quiet': True,
        'no_warnings': True,
        'noplaylist': True,
        # bv*+ba/b: bestvideo+bestaudio (merged) АБО найкращий single-stream.
        # Це дає реальний original-bitrate замість 'best' (який часто беретав
        # progressive 720p при доступному 1080p+).
        'format': 'bv*+ba/b/best',
        'merge_output_format': 'mp4',
        'progress_hooks': [on_progress],
        'cookiesfrombrowser': None,
        'http_headers': {
            'User-Agent': (
                'Mozilla/5.0 (Windows NT 10.0; Win64; x64) '
                'AppleWebKit/537.36 (KHTML, like Gecko) '
                'Chrome/120.0.0.0 Safari/537.36'
            ),
        },
        'socket_timeout': 30,
        'retries': 3,
    }
    # Якщо юзер залив cookies — приєднаємо їх до запиту
    ck = _user_cookies_file()
    if ck:
        opts['cookiefile'] = str(ck)
        print(f"[download] using user cookies: {ck}")
    return opts


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


# Cobalt має багато community-instances. Головний api.cobalt.tools часто
# rate-limited або зовсім вимкнений. Пробуємо по черзі — перша що дасть
# відповідь з download URL виграла.
COBALT_INSTANCES = [
    'https://api.cobalt.tools/',
    'https://cobalt.callow.uk/api/json',
    'https://co.eepy.today/api/json',
    'https://cobalt-api.kwiatekmiki.com/api/json',
    'https://cobalt.synzr.ru/api/json',
]


def _try_cobalt(url: str, job_id: str, platform: str) -> Optional[str]:
    """Universal fallback через cobalt.tools та community-mirrors.

    Пробуємо ~5 інстансів. Перша відповідь зі статусом tunnel/redirect та
    URL виграла. Без cookies, без login.
    """
    import json
    import urllib.request

    download_url = None
    used_instance = None

    for instance in COBALT_INSTANCES:
        try:
            payload = json.dumps({
                'url': url,
                'videoQuality': 'max',
                'audioFormat': 'best',
                'filenameStyle': 'basic',
            }).encode('utf-8')
            req = urllib.request.Request(
                instance,
                data=payload,
                headers={
                    'Accept':       'application/json',
                    'Content-Type': 'application/json',
                    'User-Agent':   'Mozilla/5.0 (Windows NT 10.0; Win64; x64) '
                                    'AppleWebKit/537.36 Chrome/120.0.0.0',
                },
                method='POST',
            )
            with urllib.request.urlopen(req, timeout=15) as resp:
                data = json.loads(resp.read().decode('utf-8'))
        except Exception as e:
            print(f"[download] cobalt {instance}: {type(e).__name__}: {e}")
            continue

        status = data.get('status')
        if status == 'error':
            print(f"[download] cobalt {instance} rejected: "
                  f"{data.get('error', data.get('text'))}")
            continue

        candidate_url = data.get('url')
        if candidate_url:
            download_url = candidate_url
            used_instance = instance
            print(f"[download] cobalt success via {instance} (status={status})")
            break
        else:
            print(f"[download] cobalt {instance}: no url (status={status})")

    if not download_url:
        print(f"[download] all {len(COBALT_INSTANCES)} cobalt instances failed")
        return None

    out_dir = _downloads_dir()
    out_path = out_dir / f"{job_id}_{platform}.mp4"
    try:
        _upd(job_id, status='downloading', progress=20)
        dl_req = urllib.request.Request(
            download_url,
            headers={'User-Agent': 'Mozilla/5.0'},
        )
        with urllib.request.urlopen(dl_req, timeout=120) as resp:
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
                        pct = int(20 + (downloaded * 70 / total))
                        _upd(job_id, progress=pct, downloaded=downloaded, total=total)
        _upd(job_id, progress=95)
        print(f"[download] cobalt downloaded {downloaded}B from {used_instance}")
        return str(out_path)
    except Exception as e:
        print(f"[download] cobalt download stream failed: {e}")
        return None


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
        # КЛЮЧОВЕ: &hd=1 змушує tikwm повернути hdplay (1080p) якщо воно є.
        # Без цього параметра tikwm часто приховує HD за payment-режимом.
        encoded = urllib.request.quote(url, safe=':/?&=')
        api_url = f"https://www.tikwm.com/api/?url={encoded}&hd=1"
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
    # Детальний лог: розміри з API щоб юзер бачив що насправді доступно
    sz_play   = info.get('size', 0)
    sz_hdplay = info.get('hd_size', 0)
    print(f"[download] tikwm sizes: play={sz_play}B hdplay={sz_hdplay}B → chose: {used_quality}")
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


def _try_yt_dlp(url: str, job_id: str) -> Optional[str]:
    """yt-dlp у власній функції щоб можна було викликати окремо для TikTok-first.

    Повертає шлях до файлу або None при помилці.
    """
    import yt_dlp

    out_dir = _downloads_dir()
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
            _upd(job_id, status='merging', progress=95)

    opts = _yt_dlp_options(out_template, progress_hook)
    with yt_dlp.YoutubeDL(opts) as ydl:
        info = ydl.extract_info(url, download=True)
        file_path = ydl.prepare_filename(info)
        if not Path(file_path).exists():
            mp4_path = re.sub(r'\.[^.]+$', '.mp4', file_path)
            if Path(mp4_path).exists():
                file_path = mp4_path
    return file_path if Path(file_path).exists() else None


def _run_download(job_id: str, url: str) -> None:
    try:
        platform = detect_platform(url)
        _upd(job_id, status='downloading', progress=5)

        # СТРАТЕГІЯ ЯКОСТІ:
        #   TikTok: yt-dlp ПЕРШИЙ (тягне оригінальний 1080p H.264) →
        #           якщо TikTok заблокував → tikwm (часто 720p) →
        #           cobalt як останній шанс.
        #   YouTube / Instagram: cobalt спочатку (yt-dlp ці платформи
        #           блокують без cookies), yt-dlp лише з cookies.
        if platform == 'tiktok':
            # yt-dlp first — найвища якість через TikTok's own player API
            try:
                res = _try_yt_dlp(url, job_id)
                if res and Path(res).exists():
                    sz = Path(res).stat().st_size
                    print(f"[download] yt-dlp TikTok success: {sz} bytes")
                    _upd(job_id, status='done', progress=100, file_path=res)
                    return
            except Exception as e:
                print(f"[download] yt-dlp TikTok failed: {type(e).__name__}: {e}")

            print("[download] yt-dlp failed for TikTok, falling back to tikwm")
            res = _try_tikwm(url, job_id)
            if res and Path(res).exists():
                sz = Path(res).stat().st_size
                print(f"[download] tikwm TikTok success: {sz} bytes")
                _upd(job_id, status='done', progress=100, file_path=res)
                return
            print("[download] tikwm failed, trying cobalt")
            res = _try_cobalt(url, job_id, platform)
            if res and Path(res).exists():
                _upd(job_id, status='done', progress=100, file_path=res)
                return
            print("[download] cobalt failed, falling back to yt-dlp generic")

        elif platform in ('youtube', 'instagram'):
            # YouTube / IG агресивно блокують yt-dlp як бота.
            # cobalt не має цих проблем (працює через серверну реалізацію).
            res = _try_cobalt(url, job_id, platform)
            if res and Path(res).exists():
                _upd(job_id, status='done', progress=100, file_path=res)
                return
            print(f"[download] cobalt failed for {platform}, falling back to yt-dlp")

        # Fallback: yt-dlp (для YT/IG потребує cookies; для TikTok вже намагались)
        _upd(job_id, status='downloading', progress=1)
        file_path = _try_yt_dlp(url, job_id)
        if not file_path:
            raise RuntimeError("yt-dlp не зміг завантажити відео")
        _upd(job_id, status='done', progress=100, file_path=file_path)

    except Exception as e:
        err = str(e)
        # Друк типу помилки у server.log для діагностики
        print(f"[download] job={job_id} failed: {type(e).__name__}: {err}")
        # Юзер-френдлі повідомлення для типових кейсів
        platform = detect_platform(url)
        if 'Sign in' in err or 'not a bot' in err or 'cookies' in err.lower():
            err = (f"YouTube тимчасово блокує анонімне скачування для цього "
                   f"відео. Спробуй інше посилання або TikTok/Instagram.")
        elif 'format is not available' in err:
            err = (f"{platform.title()} не віддає форматів без авторизації. "
                   f"Спробуй інше відео — деякі публічні працюють.")
        elif 'Private video' in err or 'unavailable' in err.lower():
            err = f"Відео приватне або видалене."
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
