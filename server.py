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


# ── HTTP сервер (multi-threaded) ──────────────────────────────────────────────

class Server(socketserver.ThreadingMixIn, HTTPServer):
    daemon_threads = True
    allow_reuse_address = True


class Handler(BaseHTTPRequestHandler):

    # ── Routing ───────────────────────────────────────────────────────────────

    def do_GET(self):
        p = urlparse(self.path).path
        if p in ('/', '/index.html'):
            self._file(BASE_DIR / 'index.html', 'text/html; charset=utf-8')
        elif p == '/api/auth/status':
            self._auth_status()
        elif p.startswith('/events/'):
            self._sse_stream(p[8:])
        elif p.startswith('/download/'):
            self._download(p[10:])
        else:
            self.send_error(404)

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
        from auth import is_activated, get_registration, get_machine_id
        reg = get_registration()
        self._json(200, {
            'activated':  is_activated(),
            'registered': reg is not None,
            'machine_id': get_machine_id(),
            'name':       reg[2] if reg else '',
            'contact':    reg[3] if reg else '',
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

    # ── Утиліти ───────────────────────────────────────────────────────────────

    def _file(self, path, ct):
        try:
            data = path.read_bytes()
            self.send_response(200)
            self.send_header('Content-Type',   ct)
            self.send_header('Content-Length', str(len(data)))
            self.end_headers()
            self.wfile.write(data)
        except Exception as e:
            self.send_error(500, str(e))

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

def main():
    # Перевіряємо FFmpeg перед стартом
    check_ffmpeg()

    url = f'http://{HOST}:{PORT}'
    print(f'\n  🎬  Video Uniquifier — запущено')
    print(f'  🌐  {url}')
    print(f'  ⌨️   Зупинити: Ctrl+C\n')

    # Відкриваємо браузер через 0.8 секунди
    threading.Timer(0.8, lambda: webbrowser.open(url)).start()

    srv = Server((HOST, PORT), Handler)
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
