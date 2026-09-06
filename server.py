#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Video Uniquifier — локальний веб-сервер.
Запускає браузерний інтерфейс і виконує реальну обробку відео через FFmpeg.
"""

import io
import json
import os
import shutil
import socketserver
import subprocess
import sys
import tempfile
import threading
import time
import uuid
import webbrowser
from http.server import HTTPServer, BaseHTTPRequestHandler
from pathlib import Path
from urllib.parse import parse_qs, urlparse, quote

# Auth модулі (імпортуємо lazy у методах щоб уникнути кругових залежностей)
sys.path.insert(0, str(Path(__file__).parent))

# Форсуємо UTF-8 на Windows щоб emoji у print() не крашили сервер
# Якщо frozen (exe без консолі) — server_entry вже обробив stdout, не перезаписуємо
if sys.platform == 'win32' and not getattr(sys, 'frozen', False):
    try:
        sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
        sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8', errors='replace')
    except AttributeError:
        pass

# Імпортуємо функції з uniquifier.py
sys.path.insert(0, str(Path(__file__).parent))
from uniquifier import (
    check_ffmpeg,
    get_video_info,
    get_random_params,
    build_ffmpeg_command,
    file_md5,
)

HOST = '127.0.0.1'
PORT = 7474
BASE_DIR = Path(__file__).parent

# Сховище задач: {job_id: {...}}
_jobs: dict = {}
_lock = threading.Lock()

NO_WINDOW_KW = {}
if sys.platform == 'win32' and hasattr(subprocess, 'CREATE_NO_WINDOW'):
    NO_WINDOW_KW['creationflags'] = subprocess.CREATE_NO_WINDOW


# ── HTTP сервер (multi-threaded) ──────────────────────────────────────────────

class Server(socketserver.ThreadingMixIn, HTTPServer):
    daemon_threads = True
    allow_reuse_address = False


class Handler(BaseHTTPRequestHandler):

    # ── Routing ───────────────────────────────────────────────────────────────

    def do_GET(self):
        p = urlparse(self.path).path
        if p in ('/', '/index.html'):
            self._file(BASE_DIR / 'index.html', 'text/html; charset=utf-8')
        elif p == '/api/auth/status':
            self._auth_status()
        elif p == '/api/runtime':
            self._runtime_status()
        elif p.startswith('/assets/'):
            self._asset(p[8:])
        elif p.startswith('/fonts/'):
            self._font(p[7:])
        elif p.startswith('/events/'):
            self._sse_stream(p[8:])
        elif p.startswith('/download/'):
            self._download(p[10:])
        elif p.startswith('/api/download/status/'):
            self._download_status(p[len('/api/download/status/'):])
        elif p.startswith('/api/download/file/'):
            self._download_file(p[len('/api/download/file/'):])
        elif p == '/api/diskinfo':
            self._disk_info()
        elif p == '/api/cookies/status':
            self._cookies_status()
        else:
            self.send_error(404)

    # ── Шрифти ────────────────────────────────────────────────────────────────

    def _font(self, name: str):
        """Сервує .ttf/.otf файли з папки fonts/ поряд з index.html.

        Безпека: дозволяємо тільки прості імена (без / .. \\), щоб не
        дати доступу до інших файлів через path traversal.
        """
        from pathlib import Path as _P
        safe = _P(name).name  # відкидає будь-які / або \
        if not safe or not (safe.endswith('.ttf') or safe.endswith('.otf')):
            self.send_error(404)
            return
        font_path = BASE_DIR / 'fonts' / safe
        if not font_path.exists():
            self.send_error(404)
            return
        ct = 'font/ttf' if safe.endswith('.ttf') else 'font/otf'
        self._file(font_path, ct)

    def _asset(self, name: str):
        from pathlib import Path as _P
        safe = _P(name).name
        if safe != 'brand-logo.png':
            self.send_error(404)
            return
        asset_path = BASE_DIR / 'assets' / safe
        if not asset_path.exists():
            self.send_error(404)
            return
        self._file(asset_path, 'image/png')

    def do_POST(self):
        p = urlparse(self.path).path
        if p == '/process':
            self._upload()
        elif p == '/api/auth/register':
            self._auth_register()
        elif p == '/api/auth/activate':
            self._auth_activate()
        elif p == '/api/admin/generate-code':
            self._admin_generate_code()
        elif p == '/api/download':
            self._start_download()
        elif p == '/api/clientlog':
            self._client_log()
        elif p == '/api/cookies/upload':
            self._cookies_upload()
        elif p == '/api/cookies/delete':
            self._cookies_delete()
        elif p.startswith('/cancel/'):
            self._cancel(p[8:])
        else:
            self.send_error(404)

    def do_OPTIONS(self):
        self.send_response(200)
        self.send_header('Access-Control-Allow-Origin', '*')
        self.send_header('Access-Control-Allow-Methods', 'GET, POST, OPTIONS')
        self.end_headers()

    # ── Auth API ──────────────────────────────────────────────────────────────

    def _auth_status(self):
        from auth import is_activated, get_registration, get_machine_id, get_jwt
        reg = get_registration()
        jwt_pair = get_jwt()
        self._json(200, {
            'activated':  is_activated(),
            'registered': reg is not None,
            'machine_id': get_machine_id(),
            'name':       reg[2] if reg else '',
            'contact':    reg[3] if reg else '',
            # JWT-роль — потрібна frontend'у для приховування/показу admin features.
            # Якщо JWT нема (бот був недоступний при активації, або стара версія) —
            # role='STUDENT' за замовчуванням, admin-features недоступні.
            # Master-password все ще працює як emergency fallback через
            # _hash_password в validate_and_activate.
            'role':       (jwt_pair[1] if jwt_pair else 'STUDENT'),
        })

    def _runtime_status(self):
        self._json(200, {
            'ok': True,
            'pid': os.getpid(),
            'token': os.environ.get('SLENG_SERVER_TOKEN', ''),
        })

    def _auth_register(self):
        length = int(self.headers.get('Content-Length', 0))
        try:
            body    = json.loads(self.rfile.read(length).decode('utf-8'))
        except Exception:
            self._json(400, {'error': 'Невірний запит'})
            return
        name    = body.get('name', '').strip()
        contact = body.get('contact', '').strip()
        if not name:
            self._json(400, {'error': "Введіть ваше ім'я"})
            return
        from auth import save_registration, get_machine_id
        from bot_notify import notify_registration
        save_registration(name, contact)
        mid = get_machine_id()
        notify_registration(name, contact, mid)
        self._json(200, {'ok': True, 'machine_id': mid})

    def _auth_activate(self):
        length = int(self.headers.get('Content-Length', 0))
        try:
            body = json.loads(self.rfile.read(length).decode('utf-8'))
        except Exception:
            self._json(400, {'error': 'Невірний запит'})
            return
        code = body.get('code', '').strip()
        if not code:
            self._json(400, {'error': 'Введіть код активації'})
            return
        from auth import validate_and_activate
        if validate_and_activate(code):
            self._json(200, {'ok': True})
        else:
            self._json(200, {'ok': False, 'error': 'Невірний код активації'})

    def _admin_generate_code(self):
        length = int(self.headers.get('Content-Length', 0))
        try:
            body = json.loads(self.rfile.read(length).decode('utf-8'))
        except Exception:
            self._json(400, {'error': 'Невірний запит'})
            return
        password   = body.get('password', '')
        machine_id = body.get('machine_id', '').strip()
        from auth import check_admin_password, generate_activation_code
        if not check_admin_password(password):
            self._json(403, {'error': 'Невірний пароль'})
            return
        if not machine_id:
            self._json(400, {'error': 'Введіть Machine ID'})
            return
        code = generate_activation_code(machine_id)
        self._json(200, {'code': code})

    # ── Завантаження файлу та старт обробки ──────────────────────────────────

    def _upload(self):
        from auth import is_activated
        if not is_activated():
            self._json(403, {'error': 'Не авторизовано. Активуй додаток.'})
            return

        qs = parse_qs(urlparse(self.path).query)
        g = lambda k, d='': qs.get(k, [d])[0]

        filename   = g('name', 'video.mp4')
        preset     = g('preset', 'medium')
        keep_audio = g('keepAudio', 'false') == 'true'
        no_rot     = g('noRotation', 'false') == 'true'
        show_hash  = g('showHash',  'false') == 'true'
        seed_s     = g('seed', '')
        seed       = int(seed_s) if seed_s.isdigit() else None

        length = int(self.headers.get('Content-Length', 0))
        if length == 0:
            self._json(400, {'error': 'Порожній файл'})
            return

        job_id  = uuid.uuid4().hex[:12]
        tmp_dir = Path(tempfile.mkdtemp(prefix='uniq_'))
        safe    = Path(filename).name or 'input.mp4'
        inp     = tmp_dir / safe
        out     = tmp_dir / f"{Path(safe).stem}_unique.mp4"

        # Зберігаємо файл шматками (без навантаження пам'яті)
        try:
            with open(inp, 'wb') as f:
                rem = length
                while rem > 0:
                    chunk = self.rfile.read(min(65536, rem))
                    if not chunk:
                        break
                    f.write(chunk)
                    rem -= len(chunk)
        except Exception as e:
            self._json(500, {'error': str(e)})
            return

        with _lock:
            _jobs[job_id] = {
                'status':      'running',
                'progress':    0,
                'stage':       'Підготовка...',
                'output':      None,
                'out_name':    f"{Path(safe).stem}_unique.mp4",
                'in_size':     length,
                'out_size':    0,
                'proc_time':   0.0,
                'hash_before': '',
                'hash_after':  '',
                'error':       '',
                'proc':        None,
                'tmp':         str(tmp_dir),
                'show_hash':   show_hash,
            }

        threading.Thread(
            target=_process,
            args=(job_id, inp, out, preset, keep_audio, no_rot, seed, show_hash),
            daemon=True,
        ).start()

        self._json(200, {'job_id': job_id})

    # ── SSE потік прогресу ────────────────────────────────────────────────────

    def _sse_stream(self, job_id):
        self.send_response(200)
        self.send_header('Content-Type',  'text/event-stream')
        self.send_header('Cache-Control', 'no-cache')
        self.send_header('Connection',    'keep-alive')
        self.send_header('X-Accel-Buffering', 'no')
        self.end_headers()

        last_pct = -1
        deadline = time.time() + 600  # 10 хвилин максимум

        while time.time() < deadline:
            with _lock:
                job = _jobs.get(job_id)

            if not job:
                self._event({'type': 'error', 'message': 'Job not found'})
                break

            st = job['status']
            if st == 'done':
                self._event({
                    'type':       'done',
                    'job_id':     job_id,
                    'filename':   job['out_name'],
                    'in_size':    job['in_size'],
                    'out_size':   job['out_size'],
                    'proc_time':  round(job['proc_time'], 1),
                    'hash_before': job['hash_before'],
                    'hash_after':  job['hash_after'],
                })
                break
            elif st == 'error':
                self._event({'type': 'error', 'message': job['error']})
                break
            elif st == 'cancelled':
                self._event({'type': 'error', 'message': 'Скасовано'})
                break
            elif job['progress'] != last_pct:
                last_pct = job['progress']
                self._event({'type': 'progress', 'pct': job['progress'], 'stage': job['stage']})

            time.sleep(0.15)

    def _event(self, data):
        try:
            msg = ('data: ' + json.dumps(data, ensure_ascii=False) + '\n\n').encode('utf-8')
            self.wfile.write(msg)
            self.wfile.flush()
        except Exception:
            pass

    # ── Завантаження результату ───────────────────────────────────────────────

    def _download(self, job_id):
        with _lock:
            job = _jobs.get(job_id)

        if not job or job['status'] != 'done' or not job['output']:
            self.send_error(404)
            return

        out_path = Path(job['output'])
        if not out_path.exists():
            self.send_error(404)
            return

        size     = out_path.stat().st_size
        filename = job['out_name']

        self.send_response(200)
        self.send_header('Content-Type',        'video/mp4')
        self.send_header('Content-Length',      str(size))
        self.send_header('Content-Disposition', f"attachment; filename*=UTF-8''{quote(filename)}")
        self.end_headers()

        with open(out_path, 'rb') as f:
            while True:
                chunk = f.read(65536)
                if not chunk:
                    break
                try:
                    self.wfile.write(chunk)
                except Exception:
                    break

        # Прибираємо тимчасові файли через 60 секунд
        threading.Thread(target=_cleanup, args=(job_id, 60), daemon=True).start()

    # ── Скасування задачі ─────────────────────────────────────────────────────

    def _cancel(self, job_id):
        with _lock:
            job = _jobs.get(job_id)
            if job:
                proc = job.get('proc')
                if proc:
                    try:
                        proc.kill()
                    except Exception:
                        pass
                job['status'] = 'cancelled'

        threading.Thread(target=_cleanup, args=(job_id, 2), daemon=True).start()
        self._json(200, {'ok': True})

    # ── Місце на диску ───────────────────────────────────────────────────────

    # Нижче цієї межі скачувати немає сенсу: браузерне blob-сховище Chromium
    # лежить на тому ж диску, і навіть при повністю отриманому тілі
    # response.blob() падає з TypeError 'Failed to fetch'. Ззовні це виглядає
    # як мережева помилка, хоча мережа ні до чого.
    MIN_FREE_BYTES = 500 * 1024 * 1024      # жорстка відмова
    LOW_FREE_BYTES = 2 * 1024 * 1024 * 1024  # попередження

    @staticmethod
    def _free_space():
        """(free, total, drive) для диска, де лежать завантаження."""
        from downloader import _downloads_dir
        d = _downloads_dir()
        usage = shutil.disk_usage(str(d))
        return usage.free, usage.total, str(d.drive or d.anchor)

    def _disk_info(self):
        try:
            free, total, drive = self._free_space()
        except Exception as e:
            self._json(500, {'error': 'disk_check_failed', 'detail': str(e)})
            return
        self._json(200, {
            'free': free,
            'total': total,
            'drive': drive,
            'free_gb': round(free / 1024 ** 3, 2),
            'low': free < self.LOW_FREE_BYTES,
            'critical': free < self.MIN_FREE_BYTES,
        })

    # ── Діагностика з фронтенду ──────────────────────────────────────────────

    def _client_log(self):
        """POST /api/clientlog — рендерер шле сюди діагностику, вона лягає в
        server.log поряд із серверними подіями.

        Навіщо: DevTools у production вимкнено (devTools: !app.isPackaged),
        тому помилки фронтенду не лишають ЖОДНОГО сліду. Без цього каналу
        "Failed to fetch" неможливо відрізнити від обриву тіла чи відмови
        з'єднання — сервер такий запит просто не бачить.
        """
        try:
            ln = int(self.headers.get('Content-Length', 0))
            if ln <= 0 or ln > 256 * 1024:
                self._json(400, {'error': 'bad_size'})
                return
            data = json.loads(self.rfile.read(ln).decode('utf-8', errors='replace'))
        except Exception as e:
            self._json(400, {'error': 'bad_json', 'detail': str(e)})
            return

        tag = str(data.get('tag', 'log'))[:40]
        items = data.get('data')
        if tag == 'batch' and isinstance(items, list):
            # Пачка з черги фронтенду — кожен запис окремим рядком, з часом
            # коли подія СТАЛАСЬ (а не коли дійшла — вони можуть різнитись).
            for it in items[:100]:
                ts = str(it.get('t', ''))[11:23]
                itag = str(it.get('tag', '?'))[:40]
                body = json.dumps(it.get('data'), ensure_ascii=False)[:1200]
                print(f'[client] {ts} {itag}: {body}', flush=True)
        else:
            print(f'[client] {tag}: '
                  f'{json.dumps(items, ensure_ascii=False)[:2000]}', flush=True)
        self._json(200, {'ok': True})

    # ── Download API (TikTok / Instagram / YouTube via yt-dlp) ────────────────

    def _start_download(self):
        """POST /api/download — приймає {url}, повертає {job_id}."""
        from auth import is_activated
        if not is_activated():
            self._json(403, {'error': 'not_activated'})
            return
        try:
            ln = int(self.headers.get('Content-Length', 0))
            body = self.rfile.read(ln).decode('utf-8') if ln else '{}'
            data = json.loads(body)
        except Exception:
            self._json(400, {'error': 'invalid_json'})
            return

        url = (data.get('url') or '').strip()
        if not url or not (url.startswith('http://') or url.startswith('https://')):
            self._json(400, {'error': 'invalid_url'})
            return

        # Перевіряємо місце ДО скачування — інакше відео завантажиться, а
        # потім упаде на етапі передачі у браузер з незрозумілою помилкою.
        try:
            free, _total, drive = self._free_space()
            if free < self.MIN_FREE_BYTES:
                self._json(507, {
                    'error': 'low_disk',
                    'free': free,
                    'drive': drive,
                    'detail': (f'На диску {drive} лишилось '
                               f'{free / 1024 ** 3:.2f} ГБ. Потрібно щонайменше '
                               f'0.5 ГБ — звільни місце і спробуй ще раз.'),
                })
                return
        except Exception as e:
            print(f'[disk] перевірка місця не вдалась: {e}', flush=True)

        # Імпорт обгорнутий у try — щоб якщо downloader.py / yt-dlp не зібрався
        # PyInstaller'ом коректно, юзер бачив осмислену помилку, а не "Сервер
        # недоступний". Часті причини: yt_dlp не в hiddenimports, або
        # downloader.py не в datas.
        try:
            from downloader import start_download, detect_platform, _user_cookies_file
        except ImportError as e:
            print(f"[ERR] /api/download import failed: {e}")
            self._json(500, {
                'error': 'downloader_unavailable',
                'detail': f'Модуль завантаження не доступний: {e}'
            })
            return

        try:
            platform = detect_platform(url)
            if platform == 'unknown':
                self._json(400, {'error': 'unsupported_platform',
                                 'detail': 'Підтримуються лише TikTok, Instagram, YouTube'})
                return

            # YouTube та Instagram БЕЗ cookies = гарантована помилка.
            # Тому навіть не пробуємо качати — одразу повертаємо чітку помилку
            # з кодом, на який фронт покаже cookies-підказку.
            if platform in ('youtube', 'instagram') and not _user_cookies_file():
                name = 'YouTube' if platform == 'youtube' else 'Instagram'
                self._json(400, {
                    'error': 'cookies_required',
                    'platform': platform,
                    'detail': (f'{name} блокує анонімні запити. '
                               f'Додай свої cookies — це робиться один раз за 2 хв. '
                               f'Натисни кнопку «🍪 Налаштувати cookies» нижче.')
                })
                return

            job_id = start_download(url)
            self._json(200, {'ok': True, 'job_id': job_id, 'platform': platform})
        except Exception as e:
            print(f"[ERR] /api/download crashed: {type(e).__name__}: {e}")
            self._json(500, {
                'error': 'server_error',
                'detail': f'{type(e).__name__}: {e}'
            })

    def _download_status(self, job_id: str):
        """GET /api/download/status/<job_id> — повертає прогрес/готовність."""
        from downloader import get_job
        job = get_job(job_id)
        if not job:
            self._json(404, {'error': 'job_not_found'})
            return
        # Не повертаємо повний шлях у відповіді — лише факт готовності,
        # фронт качає файл через окремий endpoint /api/download/file/<id>
        resp = {
            'status':   job.get('status'),
            'progress': job.get('progress', 0),
            'error':    job.get('error'),
            'platform': job.get('platform'),
            'title':    job.get('title', ''),
        }
        if job.get('status') == 'done':
            resp['ready'] = True
            resp['filename'] = Path(job.get('file_path', '')).name
        self._json(200, resp)

    def _download_file(self, job_id: str):
        """GET /api/download/file/<job_id> — стрімить готовий файл у браузер."""
        from downloader import get_job
        job = get_job(job_id)
        if not job:
            self._json(404, {'error': 'job_not_found'})
            return
        if job.get('status') != 'done':
            self._json(409, {'error': 'not_ready', 'status': job.get('status')})
            return
        file_path = job.get('file_path')
        if not file_path or not Path(file_path).exists():
            self._json(404, {'error': 'file_missing', 'detail': str(file_path)})
            return
        # mp4 у 99% випадків (yt-dlp merge_output_format='mp4')
        print(f'[file] запит {job_id} -> {Path(file_path).name}', flush=True)
        self._file(Path(file_path), 'video/mp4', verbose=True)

    # ── Cookies management (для YouTube/Instagram приватного контенту) ────────

    def _cookies_path(self):
        """Шлях до user cookies.txt."""
        base = os.environ.get('LOCALAPPDATA') or os.environ.get('APPDATA') or str(Path.home())
        return Path(base) / 'SlengUniquifier' / 'cookies.txt'

    def _cookies_status(self):
        """GET /api/cookies/status — чи є файл і скільки рядків / коли заливаний."""
        p = self._cookies_path()
        if p.exists() and p.stat().st_size > 0:
            try:
                # Рахуємо тільки cookie-рядки (не коментарі/пусті)
                lines = [l for l in p.read_text(encoding='utf-8', errors='ignore').splitlines()
                         if l and not l.startswith('#')]
                self._json(200, {
                    'enabled': True,
                    'count': len(lines),
                    'size': p.stat().st_size,
                })
                return
            except Exception:
                pass
        self._json(200, {'enabled': False, 'count': 0})

    def _cookies_upload(self):
        """POST /api/cookies/upload — приймає raw text cookies.txt у body."""
        try:
            ln = int(self.headers.get('Content-Length', 0))
            if ln <= 0 or ln > 5 * 1024 * 1024:  # max 5 MB
                self._json(400, {'error': 'invalid_size'})
                return
            data = self.rfile.read(ln).decode('utf-8', errors='replace')
        except Exception:
            self._json(400, {'error': 'read_failed'})
            return

        # Базова перевірка що це справді cookies.txt формат (Netscape):
        # перший рядок або '# Netscape HTTP Cookie File' або tab-separated cookie line
        lines = [l for l in data.splitlines() if l and not l.startswith('#')]
        if not lines:
            self._json(400, {'error': 'empty_or_invalid'})
            return
        # Більшість cookie-рядків мають tab-separated 7 полів
        first_line = lines[0].split('\t')
        if len(first_line) < 6:
            self._json(400, {
                'error': 'wrong_format',
                'detail': 'Файл не схожий на Netscape cookies.txt. '
                          'Використай browser extension "Get cookies.txt LOCALLY".'
            })
            return

        p = self._cookies_path()
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(data, encoding='utf-8')
        self._json(200, {
            'ok': True,
            'count': len(lines),
            'detail': f'Збережено {len(lines)} cookie-рядків'
        })

    def _cookies_delete(self):
        """POST /api/cookies/delete — видаляє cookies.txt."""
        p = self._cookies_path()
        if p.exists():
            try:
                p.unlink()
            except Exception:
                pass
        self._json(200, {'ok': True})

    # ── Утиліти ───────────────────────────────────────────────────────────────

    def _file(self, path, ct, verbose=False):
        # Щойно скачане відео на Windows буває тимчасово залочене (ffmpeg ще
        # тримає хендл, антивірус сканує новий .mp4) — open() падає з
        # PermissionError. Ретраїмо ~3 с перед тим як віддати помилку.
        fh = None
        last_err = None
        for _ in range(12):
            try:
                fh = open(path, 'rb')
                break
            except FileNotFoundError as e:
                last_err = e
                break  # файла просто нема — ретраї не допоможуть
            except OSError as e:
                last_err = e
                time.sleep(0.25)
        if fh is None:
            self._json(500, {
                'error':  'file_locked',
                'detail': f'{type(last_err).__name__}: {last_err}',
            })
            return
        sent = 0
        size = -1
        t0 = time.time()
        try:
            size = os.fstat(fh.fileno()).st_size
            self.send_response(200)
            self.send_header('Content-Type',   ct)
            self.send_header('Content-Length', str(size))
            self.end_headers()
            # Стрімимо шматками — 30-мегабайтне відео не тримаємо в RAM цілком
            while True:
                chunk = fh.read(256 * 1024)
                if not chunk:
                    break
                self.wfile.write(chunk)
                sent += len(chunk)
        except Exception as e:
            # Заголовки вже пішли — лишається тільки лог і обрив з'єднання.
            # log_message() тут вимкнено, тому пишемо явно: без цього рядка
            # обрив тіла на півдорозі не лишає ЖОДНОГО сліду в лозі.
            print(f'[ERR] _file {path.name}: {type(e).__name__}: {e} '
                  f'(віддано {sent} з {size} байт за {time.time() - t0:.1f}с)', flush=True)
        else:
            if verbose:
                print(f'[file] {path.name}: {sent} байт за {time.time() - t0:.2f}с', flush=True)
        finally:
            fh.close()

    def _json(self, code, data):
        body = json.dumps(data, ensure_ascii=False).encode('utf-8')
        self.send_response(code)
        self.send_header('Content-Type',   'application/json')
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *_):
        pass  # тихий режим


# ── Обробка відео у фоновому потоці ──────────────────────────────────────────

def _stage_label(pct: int) -> str:
    if pct < 12: return 'Аналіз відео...'
    if pct < 22: return 'Очищення метаданих...'
    if pct < 65: return 'Застосування відео-фільтрів...'
    if pct < 78: return 'Обробка аудіо...'
    if pct < 93: return 'Перекодування...'
    return 'Фіналізація...'


def _upd(job_id: str, **kw):
    with _lock:
        if job_id in _jobs:
            _jobs[job_id].update(kw)


def _process(job_id, inp: Path, out: Path, preset, keep_audio, no_rot, seed, show_hash):
    try:
        # MD5 вхідного файлу
        if show_hash:
            _upd(job_id, stage='Обчислення хешу...')
            h_before = file_md5(inp)
            _upd(job_id, hash_before=h_before)

        _upd(job_id, progress=3, stage='Аналіз відео...')

        info = get_video_info(inp)
        if not info:
            _upd(job_id, status='error', error='Не вдалося прочитати відео файл')
            return

        params = get_random_params(preset, seed)
        cmd    = build_ffmpeg_command(inp, out, params, info,
                                     keep_audio=keep_audio, no_rotation=no_rot)

        # Вставляємо -progress pipe:1 перед вихідним файлом
        cmd = cmd[:-1] + ['-progress', 'pipe:1'] + cmd[-1:]


        duration = 0.0
        try:
            duration = float(info.get('format', {}).get('duration', 0))
        except Exception:
            pass

        _upd(job_id, progress=6, stage='Запуск FFmpeg...')
        t0 = time.time()

        proc = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True, encoding='utf-8', errors='replace',
            **NO_WINDOW_KW,
        )
        _upd(job_id, proc=proc)

        # Читаємо stderr у фоні (уникаємо дедлоку через переповнений буфер)
        stderr_buf = []
        def _drain():
            for ln in proc.stderr:
                stderr_buf.append(ln)
        threading.Thread(target=_drain, daemon=True).start()

        # Парсимо прогрес зі stdout
        for line in proc.stdout:
            line = line.strip()
            if line.startswith('out_time_us='):
                try:
                    us = int(line.split('=')[1])
                    if us > 0 and duration > 0:
                        pct = min(95, int(us / (duration * 1_000_000) * 90) + 6)
                        _upd(job_id, progress=pct, stage=_stage_label(pct))
                except Exception:
                    pass

        proc.wait()
        elapsed = time.time() - t0

        if proc.returncode != 0:
            err_msg = ''.join(stderr_buf[-5:]).strip() or f'код {proc.returncode}'
            _upd(job_id, status='error',
                 error=f'FFmpeg завершився з помилкою ({proc.returncode}): {err_msg}')
            return

        if not out.exists() or out.stat().st_size == 0:
            _upd(job_id, status='error', error='Вихідний файл порожній або не створено')
            return

        out_size = out.stat().st_size

        # MD5 вихідного файлу
        h_after = ''
        if show_hash:
            _upd(job_id, stage='Перевірка файлу...')
            h_after = file_md5(out)

        _upd(job_id,
             status='done', progress=100, stage='Готово!',
             output=str(out), out_size=out_size,
             proc_time=elapsed, hash_after=h_after)

    except Exception as e:
        _upd(job_id, status='error', error=str(e))


def _cleanup(job_id: str, delay: int = 0):
    if delay:
        time.sleep(delay)
    with _lock:
        job = _jobs.pop(job_id, None)
    if job:
        tmp = job.get('tmp')
        if tmp and Path(tmp).exists():
            try:
                shutil.rmtree(tmp)
            except Exception:
                pass


# ── Запуск ────────────────────────────────────────────────────────────────────

def _downloads_janitor():
    """Прибирає старі завантаження — при старті і далі раз на годину.

    Раніше cleanup_old_downloads() існувала, але НЕ викликалась ніде (докстрінг
    обіцяв cron, якого не було). За три місяці папка набирала 1.4 ГБ і забивала
    системний диск, а повний диск ламав передачу відео в браузер.
    """
    while True:
        try:
            from downloader import cleanup_old_downloads
            n = cleanup_old_downloads(max_age_hours=24)
            if n:
                print(f'[janitor] видалено старих завантажень: {n}', flush=True)
        except Exception as e:
            print(f'[janitor] помилка прибирання: {type(e).__name__}: {e}', flush=True)
        time.sleep(3600)


def main():
    if not check_ffmpeg():
        raise RuntimeError(
            'FFmpeg/ffprobe not found. Put ffmpeg.exe and ffprobe.exe next to server.exe '
            'or set FFMPEG_DIR.'
        )

    url = f'http://{HOST}:{PORT}'
    try:
        srv = Server((HOST, PORT), Handler)
    except OSError as e:
        raise RuntimeError(
            f'Port {PORT} is already in use. Close other Sleng Uniquifier windows and try again.'
        ) from e

    print(f'\n  🎬  Video Uniquifier — запущено')
    print(f'  🌐  {url}')
    print(f'  ⌨️   Зупинити: Ctrl+C\n')

    # Відкриваємо браузер через 0.8 секунди — тільки при ручному запуску.
    # Electron-збірка стартує цей сервер з SLENG_NO_BROWSER=1, бо вона сама
    # відкриває локальну адресу всередині свого BrowserWindow.
    if os.environ.get('SLENG_NO_BROWSER') != '1':
        threading.Timer(0.8, lambda: webbrowser.open(url)).start()

    # Прибиральник старих завантажень (див. _downloads_janitor)
    threading.Thread(target=_downloads_janitor, daemon=True).start()

    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print('\n  Сервер зупинено.')
        srv.shutdown()


if __name__ == '__main__':
    import traceback
    log_path = Path(__file__).parent / 'server_error.log'
    try:
        main()
    except Exception:
        err = traceback.format_exc()
        log_path.write_text(err, encoding='utf-8')
        print(f'\n  ПОМИЛКА:\n{err}')
        input('  Натисни Enter щоб закрити...')
