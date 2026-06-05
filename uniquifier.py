#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Video Uniquifier v1.0
Змінює цифровий відбиток відео без видимих змін якості та вигляду.
Обходить системи детекції копій TikTok, Instagram, YouTube через зміну
метаданих, перцептуального хешу, аудіо-фінгерпринту та структури кадрів.
"""

import argparse
import hashlib
import io
import json
import math
import os
import platform
import random
import string
import subprocess
import sys
import time
from pathlib import Path
from typing import Dict, List, Optional, Tuple

# Форсуємо UTF-8 на Windows — тільки при прямому запуску, не при імпорті
if __name__ == '__main__' and sys.platform == 'win32':
    try:
        sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
        sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8', errors='replace')
    except AttributeError:
        pass

# ─── Пошук ffmpeg / ffprobe ───────────────────────────────────────────────────

def _find_tool(name: str) -> str:
    """
    Повертає абсолютний шлях до ffmpeg або ffprobe.

    Порядок пошуку:
    1. Поруч з server.exe (папка resources встановленого Electron-застосунку).
       Це головний варіант для кінцевих користувачів.
    2. Змінна середовища FFMPEG_DIR (для CI або ручного налаштування).
    3. Просто ім'я — якщо ffmpeg є у системному PATH
       (розробницьке середовище або ручна установка).
    """
    exe = f"{name}.exe" if sys.platform == "win32" else name

    # 1. Поруч з виконуваним файлом (PyInstaller frozen або звичайний .py запуск)
    candidates = [
        Path(sys.executable).parent / exe,
    ]
    # Якщо PyInstaller витяг файли у тимчасову папку, перевіряємо і її
    meipass = getattr(sys, '_MEIPASS', None)
    if meipass:
        candidates.append(Path(meipass) / exe)

    for c in candidates:
        if c.exists():
            return str(c)

    # 2. FFMPEG_DIR змінна середовища
    ffmpeg_dir = os.environ.get("FFMPEG_DIR", "")
    if ffmpeg_dir:
        c = Path(ffmpeg_dir) / exe
        if c.exists():
            return str(c)

    # 3. Системний PATH (fallback)
    return name


FFMPEG  = _find_tool("ffmpeg")
FFPROBE = _find_tool("ffprobe")

NO_WINDOW_KW = {}
if sys.platform == "win32" and hasattr(subprocess, "CREATE_NO_WINDOW"):
    NO_WINDOW_KW["creationflags"] = subprocess.CREATE_NO_WINDOW

# ─── Константи ────────────────────────────────────────────────────────────────

VIDEO_EXTENSIONS = {'.mp4', '.mov', '.avi', '.mkv', '.webm', '.flv', '.wmv', '.m4v'}

# Пресети: кожне числове значення — МАКСИМУМ.
# Реальне значення вибирається випадково від 0 (або мінімуму) до максимуму.
PRESETS: Dict[str, Dict] = {
    "stealth": {
        # Ультра-легкий — тільки перекодування + очистка метаданих.
        # Жодних візуальних чи аудіо-фільтрів.
        "noise_amount":      0.0,
        "brightness_shift":  0.0,
        "saturation_shift":  0.0,
        "contrast_shift":    0.0,
        "gamma_shift":       0.0,
        "crop_pixels":       0,
        "rotation_degrees":  0.0,
        "speed_factor":      1.0,
        "audio_pitch_cents": 0,
        "audio_noise_db":    0,
        "pixel_shift":       0,
    },
    "light": {
        # Мінімальні зміни — для більшості платформ достатньо.
        "noise_amount":      0.7,
        "brightness_shift":  0.005,
        "saturation_shift":  0.01,
        "contrast_shift":    0.005,
        "gamma_shift":       0.005,
        "crop_pixels":       1,
        "rotation_degrees":  0.0,
        "speed_factor":      1.0,
        "audio_pitch_cents": 10,
        "audio_noise_db":    -60,
        "pixel_shift":       0,
    },
    "medium": {
        # Збалансований (за замовчуванням) — рекомендований для TikTok/Instagram.
        "noise_amount":      1.5,
        "brightness_shift":  0.01,
        "saturation_shift":  0.02,
        "contrast_shift":    0.008,
        "gamma_shift":       0.008,
        "crop_pixels":       2,
        "rotation_degrees":  0.15,
        "speed_factor":      1.003,
        "audio_pitch_cents": 20,
        "audio_noise_db":    -55,
        "pixel_shift":       1,
    },
    "hard": {
        # Максимальна унікалізація — для найбільш агресивних систем детекції.
        "noise_amount":      2.5,
        "brightness_shift":  0.02,
        "saturation_shift":  0.03,
        "contrast_shift":    0.01,
        "gamma_shift":       0.01,
        "crop_pixels":       4,
        "rotation_degrees":  0.25,
        "speed_factor":      1.005,
        "audio_pitch_cents": 30,
        "audio_noise_db":    -50,
        "pixel_shift":       2,
    },
}


# ─── Перевірка FFmpeg ──────────────────────────────────────────────────────────

def check_ffmpeg() -> bool:
    """Перевірка наявності FFmpeg та ffprobe.
    Повертає True якщо обидва знайдені, False якщо ні (не завершує процес).

    Якщо запуск падає — логуємо ВСЮ причину (exception/stderr/exit code)
    щоб діагностувати: антивірус, диск переповнений, відсутні DLL, тощо.
    """
    missing = []
    for tool, path in [('ffmpeg', FFMPEG), ('ffprobe', FFPROBE)]:
        try:
            result = subprocess.run(
                [path, '-version'],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                timeout=10,
                **NO_WINDOW_KW,
            )
            if result.returncode != 0:
                stderr_tail = result.stderr.decode('utf-8', errors='replace').strip()[-300:]
                print(f"[ERR] {tool} exited with code {result.returncode}. "
                      f"path={path}. stderr={stderr_tail!r}")
                missing.append(tool)
            else:
                # Перші 60 символів — щоб переконатись що це справді ffmpeg
                head = result.stdout.decode('utf-8', errors='replace').splitlines()[0][:80]
                print(f"[OK] {tool} runs ({path}): {head}")
        except FileNotFoundError as e:
            print(f"[ERR] {tool} FileNotFoundError: path={path}, msg={e}")
            missing.append(tool)
        except PermissionError as e:
            print(f"[ERR] {tool} PermissionError (anti-virus block?): path={path}, msg={e}")
            missing.append(tool)
        except OSError as e:
            print(f"[ERR] {tool} OSError (disk full? DLL missing?): "
                  f"path={path}, errno={e.errno}, msg={e}")
            missing.append(tool)
        except subprocess.TimeoutExpired:
            print(f"[ERR] {tool} timeout after 10s: path={path}")
            missing.append(tool)
        except Exception as e:
            print(f"[ERR] {tool} unexpected {type(e).__name__}: path={path}, msg={e}")
            missing.append(tool)

    if missing:
        print(f"[WARN] FFmpeg не знайдено: {', '.join(missing)}")
        return False
    return True


# ─── Інформація про відео ──────────────────────────────────────────────────────

def get_video_info(input_path: Path) -> Optional[Dict]:
    """Отримання повної інформації про відео через ffprobe.
    Повертає dict з полями streams і format, або None при помилці.

    Усі exception'и логуються — інакше «Не вдалося прочитати відео файл»
    залишає юзера без жодного діагностичного сигналу.
    """
    try:
        # Тут -v error замість -v quiet — щоб бачити РЕАЛЬНІ помилки
        # парсингу відео у stderr (corrupted, unsupported codec, тощо).
        result = subprocess.run(
            [
                FFPROBE,
                '-v', 'error',
                '-print_format', 'json',
                '-show_streams',
                '-show_format',
                str(input_path),
            ],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=60,
            **NO_WINDOW_KW,
        )
        if result.returncode != 0:
            err = result.stderr.decode('utf-8', errors='replace').strip()
            print(f"[ERR] ffprobe failed for {input_path}: "
                  f"exit={result.returncode}, stderr={err[-400:]!r}")
            return None
        data = json.loads(result.stdout.decode('utf-8', errors='replace'))
        return data
    except FileNotFoundError as e:
        print(f"[ERR] ffprobe FileNotFoundError: path={FFPROBE}, file={input_path}, msg={e}")
        return None
    except PermissionError as e:
        print(f"[ERR] ffprobe PermissionError (anti-virus?): file={input_path}, msg={e}")
        return None
    except OSError as e:
        print(f"[ERR] ffprobe OSError (disk full?): "
              f"file={input_path}, errno={e.errno}, msg={e}")
        return None
    except subprocess.TimeoutExpired:
        print(f"[ERR] ffprobe timeout (60s) on {input_path}")
        return None
    except json.JSONDecodeError as e:
        print(f"[ERR] ffprobe returned invalid JSON: file={input_path}, msg={e}")
        return None
    except Exception as e:
        print(f"[ERR] ffprobe unexpected {type(e).__name__}: file={input_path}, msg={e}")
        return None


# ─── Генерація випадкових параметрів ──────────────────────────────────────────

def get_random_params(preset_name: str, seed: Optional[int] = None) -> Dict:
    """На основі пресету генерує конкретні випадкові значення для всіх параметрів.
    Кожен параметр — випадкове число від мінімуму до максимуму пресету.
    Знак (±) теж випадковий де це доречно."""
    preset = PRESETS[preset_name]
    rng = random.Random(seed)

    params: Dict = {}

    # ── Візуальні фільтри ──────────────────────────────────────────────────────

    # Шум: 0 до max. Temporal шум кожного кадру різний → ламає pHash і dHash.
    params['noise_amount'] = rng.uniform(0.0, preset['noise_amount'])
    params['noise_seed'] = rng.randint(1000, 99999)

    # Яскравість: ±max. Зміна значень пікселів ламає перцептуальний хеш.
    max_b = preset['brightness_shift']
    params['brightness_shift'] = rng.uniform(-max_b, max_b) if max_b > 0 else 0.0

    # Насиченість: ±max. Зсув від нейтрального 1.0 → eq saturation = 1.0 + shift.
    max_s = preset['saturation_shift']
    params['saturation_shift'] = rng.uniform(-max_s, max_s) if max_s > 0 else 0.0

    # Контраст: ±max. Зсув від нейтрального 1.0 → eq contrast = 1.0 + shift.
    max_c = preset['contrast_shift']
    params['contrast_shift'] = rng.uniform(-max_c, max_c) if max_c > 0 else 0.0

    # Гама: ±max. Зсув від нейтрального 1.0 → eq gamma = 1.0 + shift.
    max_g = preset['gamma_shift']
    params['gamma_shift'] = rng.uniform(-max_g, max_g) if max_g > 0 else 0.0

    # Мікро-кроп: 0 до max пікселів. Зсуває сітку пікселів → ламає pHash.
    params['crop_pixels'] = rng.randint(0, max(0, preset['crop_pixels']))

    # Поворот: ±max градусів. Зсуває всі пікселі → сильно ламає pHash.
    max_r = preset['rotation_degrees']
    params['rotation_degrees'] = rng.uniform(-max_r, max_r) if max_r > 0 else 0.0

    # Субпіксельний зсув: 0 до max пікселів.
    max_ps = preset['pixel_shift']
    params['pixel_shift'] = rng.randint(0, max(0, max_ps))
    if params['pixel_shift'] > 0:
        params['pixel_shift_x'] = rng.randint(0, params['pixel_shift'])
        params['pixel_shift_y'] = rng.randint(0, params['pixel_shift'])
    else:
        params['pixel_shift_x'] = 0
        params['pixel_shift_y'] = 0

    # ── Швидкість ─────────────────────────────────────────────────────────────

    # Швидкість відео: навколо 1.0 з відхиленням ±(max-1.0).
    # setpts змінює PTS кожного кадру → темпоральний фінгерпринт стає унікальним.
    speed_max = preset['speed_factor']
    if speed_max > 1.0:
        delta = speed_max - 1.0
        params['speed_factor'] = rng.uniform(1.0 - delta, 1.0 + delta)
    else:
        params['speed_factor'] = 1.0

    # ── Аудіо фільтри ─────────────────────────────────────────────────────────

    # Пітч: ±max центів. Хромапринт будується на частотному спектрі;
    # навіть 10 центів зміни повністю ламає аудіо-відбиток.
    max_pitch = preset['audio_pitch_cents']
    params['audio_pitch_cents'] = rng.randint(-max_pitch, max_pitch) if max_pitch > 0 else 0

    # Рівень шуму: від (max-10dB) до max. Нечутний діапазон, але ламає Chromaprint.
    noise_db_max = preset['audio_noise_db']
    if noise_db_max != 0:
        params['audio_noise_db'] = rng.uniform(noise_db_max - 10, noise_db_max)
    else:
        params['audio_noise_db'] = 0

    # Бітрейт аудіо: рівне число від 126k до 196k. Різний бітрейт → різний бітстрім.
    params['audio_bitrate'] = rng.choice(range(63, 99)) * 2  # 126–196k (парні значення)

    # ── Параметри кодека ──────────────────────────────────────────────────────

    # GOP розмір: різна структура ключових кадрів → інший відео-бітстрім.
    params['keyint'] = rng.choice([48, 50, 60, 72, 90])
    params['min_keyint'] = rng.choice([24, 25, 30, 36])

    # B-фрейми: більше = інший шаблон міжкадрового стиснення.
    params['bframes'] = rng.choice([2, 3, 4])

    # Reference frames: max 4 — безпечний ліміт для H.264 Level 4.1 при будь-якому
    # розмірі відео. ref=5/6 при 1080p порушує специфікацію і libx264 повертає EINVAL.
    params['ref_frames'] = rng.choice([2, 3, 4])

    # Deblock: параметри деблокінг-фільтру → інші мікро-артефакти в бітстрімі.
    params['deblock_alpha'] = rng.randint(-2, 2)
    params['deblock_beta'] = rng.randint(-2, 2)

    # Sub-pixel ME: subme 7+ потребує me=umh або вище (hex підтримує max subme=6).
    params['me_method'] = rng.choice(['hex', 'umh'])
    max_subme = 6 if params['me_method'] == 'hex' else 9
    params['subme'] = rng.choice(range(6, max_subme + 1))

    # CRF: контроль якості. 17–20 — висока якість, але щоразу інший бітстрім.
    params['crf'] = rng.randint(17, 20)
    params['codec_preset'] = rng.choice(['slow', 'medium'])

    return params


# ─── Побудова відео-фільтрів ───────────────────────────────────────────────────

def build_video_filters(
    params: Dict,
    width: int,
    height: int,
    no_rotation: bool = False,
) -> str:
    """Збирає рядок відео-фільтрів для FFmpeg -vf.
    Порядок фільтрів критично важливий для коректної роботи."""
    filters = []

    # ── 1. Мікро-кроп + масштабування ────────────────────────────────────────
    # Платформи будують pHash на 8×8 сітці пікселів. Обрізання 2px з кожного боку
    # зсуває всю сітку → хеш не збігається з оригіналом. Lanczos зберігає різкість.
    crop = params.get('crop_pixels', 0)
    if crop > 0:
        new_w = width - 2 * crop
        new_h = height - 2 * crop
        filters.append(f"crop={new_w}:{new_h}:{crop}:{crop}")
        filters.append(f"scale={width}:{height}:flags=lanczos")

    # ── 2. Мікро-поворот ─────────────────────────────────────────────────────
    # Навіть 0.1° поворот зміщує кожен піксель у просторі → pHash і dHash
    # повністю відрізняються від оригіналу. expand=0 зберігає розмір кадру.
    rotation = params.get('rotation_degrees', 0.0)
    if rotation != 0.0 and not no_rotation:
        rad = rotation * math.pi / 180.0
        filters.append(f"rotate={rad:.8f}:c=black:ow=iw:oh=ih")
        # Кроп чорних кутів після повороту. Для кутів < 1° вистачає 4px.
        trim = 4
        filters.append(f"crop={width - 2 * trim}:{height - 2 * trim}:{trim}:{trim}")
        filters.append(f"scale={width}:{height}:flags=lanczos")

    # ── 3. Субпіксельний зсув ─────────────────────────────────────────────────
    # Додаткове зміщення на 1–2px в довільному напрямку. Порушує просторову
    # відповідність між пікселями порівнюваних відео → іще один рівень захисту.
    px = params.get('pixel_shift', 0)
    sx = params.get('pixel_shift_x', 0)
    sy = params.get('pixel_shift_y', 0)
    if px > 0 and (sx > 0 or sy > 0):
        pad_w = width + px * 2
        pad_h = height + px * 2
        filters.append(f"pad={pad_w}:{pad_h}:{px}:{px}:black")
        filters.append(f"crop={width}:{height}:{sx}:{sy}")

    # ── 4. Корекція eq (яскравість / контраст / насиченість / гама) ───────────
    # pHash порівнює відносну яскравість блоків. Мікро-зміна eq змінює ці
    # відношення непомітно для ока, але достатньо щоб зламати хеш.
    b = params.get('brightness_shift', 0.0)
    s = params.get('saturation_shift', 0.0)
    c = params.get('contrast_shift', 0.0)
    g = params.get('gamma_shift', 0.0)

    eq_parts = []
    if abs(b) > 1e-7:
        eq_parts.append(f"brightness={b:.6f}")
    if abs(c) > 1e-7:
        eq_parts.append(f"contrast={1.0 + c:.6f}")
    if abs(s) > 1e-7:
        eq_parts.append(f"saturation={1.0 + s:.6f}")
    if abs(g) > 1e-7:
        # FFmpeg eq gamma: 0.1–10.0, neutral = 1.0
        gamma_val = max(0.1, min(10.0, 1.0 + g))
        eq_parts.append(f"gamma={gamma_val:.6f}")

    if eq_parts:
        filters.append(f"eq={':'.join(eq_parts)}")

    # ── 5. Шум ────────────────────────────────────────────────────────────────
    # Temporal шум (t) різний для кожного кадру → ламає і pHash і dHash.
    # Uniform (u) розподіл рівномірний → не видно на вигляд.
    # Seed гарантує відтворюваність при --seed, інакше — завжди різний.
    noise = params.get('noise_amount', 0.0)
    if noise > 0.0:
        noise_seed = params.get('noise_seed', 12345)
        filters.append(f"noise=alls={noise:.2f}:allf=t+u")

    # ── 6. Зміна швидкості (PTS) ──────────────────────────────────────────────
    # setpts множить PTS кожного кадру на коефіцієнт → темпоральна структура
    # відео змінюється. 1/speed_factor: якщо відео прискорено, PTS зменшуються.
    speed = params.get('speed_factor', 1.0)
    if abs(speed - 1.0) > 1e-6:
        pts_factor = 1.0 / speed
        filters.append(f"setpts={pts_factor:.8f}*PTS")

    # Примусова конвертація в yuv420p для сумісності з libx264 (8-біт).
    # Потрібно для 10-біт HEVC (yuv420p10le) та інших форматів.
    filters.append("format=yuv420p")

    return ','.join(filters) if filters else 'format=yuv420p'


# ─── Побудова аудіо-фільтрів ──────────────────────────────────────────────────

def build_audio_filters(
    params: Dict,
    has_audio: bool,
    sample_rate: int = 44100,
    audio_channels: int = 2,
    keep_audio: bool = False,
) -> Tuple[Optional[str], Optional[str]]:
    """Збирає аудіо-фільтри для FFmpeg.
    Повертає (af_string, filter_complex_string).
    Якщо потрібне мікшування шуму — повертає filter_complex, інакше af_string."""
    if not has_audio or keep_audio:
        return None, None

    audio_chain: List[str] = []

    # ── 1. Зміна пітча через asetrate + aresample ─────────────────────────────
    # Chromaprint будує відбиток на основі частотного спектру. asetrate змінює
    # інтерпретацію sample rate (pitch + tempo), aresample відновлює tempo → тільки
    # pitch змінюється. Навіть 10 центів → повністю інший аудіо-відбиток.
    pitch_cents = params.get('audio_pitch_cents', 0)
    if pitch_cents != 0:
        pitch_factor = 2.0 ** (pitch_cents / 1200.0)
        new_rate = int(sample_rate * pitch_factor)
        audio_chain.append(f"asetrate={new_rate}")
        audio_chain.append(f"aresample={sample_rate}")

    # ── 2. Граничні частотні фільтри ──────────────────────────────────────────
    # Видаляємо нечутний діапазон (<20Hz, >18kHz). Людське вухо не відчує різниці,
    # але спектрограма файлу змінюється → аудіо-відбиток стає унікальним.
    # Додаємо тільки якщо є інша аудіо-обробка (не для stealth/silent пресету).
    noise_db_check = params.get('audio_noise_db', 0)
    pitch_check = params.get('audio_pitch_cents', 0)
    if pitch_check != 0 or noise_db_check != 0:
        audio_chain.append("highpass=f=20")
        audio_chain.append("lowpass=f=18000")

    # ── 3. Синхронізація темпу з відео ────────────────────────────────────────
    # Якщо відео-стрім прискорено через setpts, аудіо також має прискоритися.
    # atempo змінює швидкість аудіо без зміни пітчу (використовує WSOLA алгоритм).
    speed = params.get('speed_factor', 1.0)
    if abs(speed - 1.0) > 1e-6:
        # atempo підтримує діапазон 0.5–2.0; для наших значень (0.995–1.005) OK
        audio_chain.append(f"atempo={speed:.8f}")

    # ── 4. Мікро-шум через filter_complex ────────────────────────────────────
    # anoisesrc генерує білий шум з мікро-амплітудою (-50 до -70dB).
    # amix мікшує шум з основним аудіо. Результат нечутний для вуха,
    # але Chromaprint фіксує зміни в часовій структурі сигналу.
    noise_db = params.get('audio_noise_db', 0)
    if noise_db != 0:
        amp = 10.0 ** (noise_db / 20.0)
        ch_layout = "stereo" if audio_channels >= 2 else "mono"
        chain_str = ','.join(audio_chain)

        fc_parts = []
        if chain_str:
            fc_parts.append(f"[0:a:0]{chain_str}[_amain]")
        else:
            # Навіть без інших фільтрів потрібен хоча б один для створення лейблу
            fc_parts.append(f"[0:a:0]aformat=sample_fmts=fltp[_amain]")

        # d=999999 — тривалість шуму (277+ годин), завжди довше за відео
        fc_parts.append(f"anoisesrc=d=999999:a={amp:.10f}:c=white[_anoise_raw]")
        fc_parts.append(f"[_anoise_raw]aformat=channel_layouts={ch_layout}[_anoise]")

        # duration=first — вихід закінчується разом з першим (основним) аудіо
        fc_parts.append("[_amain][_anoise]amix=inputs=2:normalize=0:duration=first[aout]")

        return None, ';'.join(fc_parts)

    af_string = ','.join(audio_chain) if audio_chain else None
    return af_string, None


# ─── Параметри кодека ──────────────────────────────────────────────────────────

def build_codec_params(params: Dict) -> List[str]:
    """Генерує параметри кодека x264.
    Різна структура GOP і параметри кодування дають різний бітстрім навіть
    для ідентичного відеовмісту — це обходить Level 4 детекцію."""
    x264_params = (
        f"keyint={params['keyint']}:"
        f"min-keyint={params['min_keyint']}:"
        f"bframes={params['bframes']}:"
        f"ref={params['ref_frames']}:"
        f"deblock={params['deblock_alpha']},{params['deblock_beta']}:"
        f"subme={params['subme']}:"
        f"me={params['me_method']}"
    )
    return [
        '-c:v', 'libx264',
        '-crf', str(params['crf']),
        '-preset', params['codec_preset'],
        '-x264-params', x264_params,
    ]


# ─── Метадані ─────────────────────────────────────────────────────────────────

def _random_string(length: int, rng: random.Random) -> str:
    """Генерує випадковий рядок із букв і цифр."""
    chars = string.ascii_letters + string.digits
    return ''.join(rng.choice(chars) for _ in range(length))


def build_metadata_params(params: Dict) -> List[str]:
    """Генерує нові випадкові метадані для MP4 контейнера.
    Видалення оригінальних тегів + нові рандомні → MD5 файлу інший,
    TikTok-специфічні теги (com.tiktok, bytedance) зникають повністю."""
    rng = random.Random(params.get('noise_seed', 0))
    creation_time = time.strftime('%Y-%m-%dT%H:%M:%S.000000Z', time.gmtime())

    return [
        # Видалення ВСІХ оригінальних метаданих включно з TikTok/ByteDance тегами
        '-map_metadata', '-1',

        # Нові випадкові метадані — платформа не знайде оригінал за тегами
        '-metadata', f'title={_random_string(12, rng)}',
        '-metadata', f'comment={_random_string(16, rng)}',
        '-metadata', f'creation_time={creation_time}',
        '-metadata', f'encoder=Lavf{rng.randint(58, 60)}.{rng.randint(0, 9)}.{rng.randint(100, 199)}',

        # Перегенерація PTS timestamps — нові значення в потоці
        '-fflags', '+genpts',

        # faststart переміщує moov atom на початок → інша структура MP4 контейнера
        '-movflags', '+faststart',
    ]


# ─── Збірка повної команди FFmpeg ─────────────────────────────────────────────

def build_ffmpeg_command(
    input_path: Path,
    output_path: Path,
    params: Dict,
    video_info: Dict,
    keep_audio: bool = False,
    no_rotation: bool = False,
) -> List[str]:
    """Збирає повну команду FFmpeg як список аргументів."""
    streams = video_info.get('streams', [])
    video_stream = next((s for s in streams if s.get('codec_type') == 'video'), None)
    audio_stream = next((s for s in streams if s.get('codec_type') == 'audio'), None)

    width = int(video_stream.get('width', 1920)) if video_stream else 1920
    height = int(video_stream.get('height', 1080)) if video_stream else 1080

    # FFmpeg 8.x автоматично застосовує Display Matrix (autorotate) перед фільтрами.
    # Якщо відео повернуто на 90°/270° — реальні розміри кадру у фільтрі є свапнуті.
    if video_stream:
        for sd in video_stream.get('side_data_list', []):
            if sd.get('side_data_type') == 'Display Matrix':
                try:
                    rot = int(round(float(sd.get('rotation', 0))))
                    if abs(rot) in (90, 270):
                        width, height = height, width
                except (ValueError, TypeError):
                    pass

    has_audio = audio_stream is not None and not keep_audio
    sample_rate = int(audio_stream.get('sample_rate', 44100)) if audio_stream else 44100
    audio_channels = int(audio_stream.get('channels', 2)) if audio_stream else 2

    # Будуємо фільтри
    vf = build_video_filters(params, width, height, no_rotation)
    af_simple, filter_complex = build_audio_filters(
        params, has_audio, sample_rate, audio_channels, keep_audio
    )

    cmd = [
        FFMPEG,
        '-y',               # Перезаписати вихідний файл без запиту
        '-i', str(input_path),
    ]

    # Аудіо: filter_complex для мікшування шуму, або простий -af
    # ВАЖЛИВО: -vf і -filter_complex не можна використовувати разом —
    # якщо є filter_complex, відео-фільтри вбудовуємо в нього.
    if filter_complex:
        if vf != 'null':
            full_fc = f"[0:v:0]{vf}[vout];{filter_complex}"
            cmd += ['-filter_complex', full_fc]
            cmd += ['-map', '[vout]', '-map', '[aout]']
        else:
            cmd += ['-filter_complex', filter_complex]
            cmd += ['-map', '0:v:0', '-map', '[aout]']
    else:
        if vf != 'null':
            cmd += ['-vf', vf]
        cmd += ['-map', '0:v:0']
        if has_audio:
            cmd += ['-map', '0:a:0']
        if af_simple:
            cmd += ['-af', af_simple]

    # Параметри відео-кодека
    cmd += build_codec_params(params)

    # Аудіо-кодек
    if filter_complex or has_audio:
        cmd += ['-c:a', 'aac', '-b:a', f"{params.get('audio_bitrate', 160)}k"]
    elif keep_audio and audio_stream:
        cmd += ['-map', '0:a:0', '-c:a', 'copy']
    else:
        cmd += ['-an']

    # Субтитри видаляємо — часто містять watermark TikTok/YouTube
    cmd += ['-sn']

    # Метадані контейнера
    cmd += build_metadata_params(params)

    cmd += [str(output_path)]
    return cmd


# ─── Утилітарні функції ────────────────────────────────────────────────────────

def format_size(size_bytes: int) -> str:
    """Форматування розміру файлу в читабельний вигляд."""
    if size_bytes < 1024:
        return f"{size_bytes} B"
    elif size_bytes < 1024 * 1024:
        return f"{size_bytes / 1024:.1f} KB"
    elif size_bytes < 1024 ** 3:
        return f"{size_bytes / (1024 * 1024):.1f} MB"
    else:
        return f"{size_bytes / (1024 ** 3):.2f} GB"


def format_duration(seconds: float) -> str:
    """Форматування тривалості в читабельний вигляд."""
    if seconds < 60:
        return f"{seconds:.1f}s"
    elif seconds < 3600:
        m = int(seconds // 60)
        s = seconds % 60
        return f"{m}m {s:.0f}s"
    else:
        h = int(seconds // 3600)
        m = int((seconds % 3600) // 60)
        return f"{h}h {m}m"


def file_md5(path: Path) -> str:
    """MD5 хеш файлу для порівняння до/після обробки."""
    md5 = hashlib.md5()
    try:
        with open(path, 'rb') as f:
            for chunk in iter(lambda: f.read(65536), b''):
                md5.update(chunk)
        return md5.hexdigest()
    except OSError:
        return 'error'


def print_banner() -> None:
    """Виводить банер програми."""
    print("╔══════════════════════════════════════════════════════════╗")
    print("║              \U0001f3ac VIDEO UNIQUIFIER v1.0                    ║")
    print("╚══════════════════════════════════════════════════════════╝")
    print()


def print_params(params: Dict, verbose: bool) -> None:
    """Виводить застосовані параметри обробки."""
    print("\u2699\ufe0f  Параметри:")

    b = params.get('brightness_shift', 0.0)
    s = params.get('saturation_shift', 0.0)
    c = params.get('contrast_shift', 0.0)
    g = params.get('gamma_shift', 0.0)

    print(f"   Яскравість:   {b:+.4f}")
    print(f"   Насиченість:  {s:+.4f}")
    print(f"   Контраст:     {c:+.4f}")
    print(f"   Гама:         {g:+.4f}")
    print(f"   Шум:          {params.get('noise_amount', 0.0):.2f} (seed: {params.get('noise_seed', 0)})")
    print(f"   Кроп:         {params.get('crop_pixels', 0)}px \u2192 scale back")
    print(f"   Поворот:      {params.get('rotation_degrees', 0.0):+.3f}\u00b0")
    print(f"   Швидкість:    \u00d7{params.get('speed_factor', 1.0):.4f}")
    print(f"   Пітч аудіо:   {params.get('audio_pitch_cents', 0):+d} центів")

    noise_db = params.get('audio_noise_db', 0)
    if noise_db != 0:
        print(f"   Аудіо шум:    {noise_db:.1f}dB")

    if verbose:
        print(f"   CRF:          {params.get('crf', 18)}")
        print(f"   GOP:          {params.get('keyint', 60)}")
        print(f"   B-frames:     {params.get('bframes', 3)}")
        print(f"   Ref frames:   {params.get('ref_frames', 4)}")
        print(f"   Deblock:      {params.get('deblock_alpha', 0)},{params.get('deblock_beta', 0)}")
        print(f"   ME method:    {params.get('me_method', 'hex')}")
        print(f"   Codec preset: {params.get('codec_preset', 'medium')}")
        print(f"   Аудіо br:     {params.get('audio_bitrate', 160)}k")
        print(f"   Піксел.зсув:  {params.get('pixel_shift', 0)}px "
              f"({params.get('pixel_shift_x', 0)},{params.get('pixel_shift_y', 0)})")

    print()


# ─── Запуск FFmpeg з прогрес-баром ────────────────────────────────────────────

def run_ffmpeg(
    cmd: List[str],
    duration: float,
    verbose: bool,
) -> int:
    """Запускає FFmpeg з відображенням прогрес-бару.
    Повертає код виходу процесу."""

    # Вставляємо -progress pipe:1 перед вихідним файлом (останній аргумент)
    progress_cmd = list(cmd)
    progress_cmd.insert(len(progress_cmd) - 1, 'pipe:1')
    progress_cmd.insert(len(progress_cmd) - 2, '-progress')

    try:
        proc = subprocess.Popen(
            progress_cmd,
            stdout=subprocess.PIPE,
            stderr=None if verbose else subprocess.PIPE,
            text=True,
            encoding='utf-8',
            errors='replace',
            **NO_WINDOW_KW,
        )
    except FileNotFoundError:
        print("❌ ffmpeg не знайдено")
        return -1
    except OSError as e:
        print(f"❌ Помилка запуску FFmpeg: {e}")
        return -1

    bar_width = 40
    last_pct = -1

    if proc.stdout:
        for line in proc.stdout:
            line = line.strip()
            if line.startswith('out_time_us='):
                try:
                    us = int(line.split('=')[1])
                    if us > 0 and duration > 0:
                        pct = min(100, int(us / (duration * 1_000_000) * 100))
                        if pct != last_pct:
                            last_pct = pct
                            filled = int(bar_width * pct / 100)
                            bar = '\u2588' * filled + '\u2591' * (bar_width - filled)
                            print(f'\r   {bar} {pct:3d}%', end='', flush=True)
                except (ValueError, IndexError):
                    pass

    proc.wait()

    if last_pct >= 0:
        bar = '\u2588' * bar_width
        print(f'\r   {bar} 100%', flush=True)
    elif duration == 0:
        # Невідома тривалість — показуємо просто завершення
        print(f'\r   {"█" * bar_width} done', flush=True)

    return proc.returncode


def run_ffmpeg_silent(cmd: List[str]) -> int:
    """Запуск FFmpeg без будь-якого виводу (для batch обробки)."""
    try:
        result = subprocess.run(
            cmd,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            **NO_WINDOW_KW,
        )
        return result.returncode
    except (FileNotFoundError, OSError):
        return -1


# ─── Обробка одного відео ─────────────────────────────────────────────────────

def uniquify_video(
    input_path: Path,
    output_path: Optional[Path] = None,
    preset_name: str = 'medium',
    verbose: bool = False,
    show_hash: bool = False,
    dry_run: bool = False,
    keep_audio: bool = False,
    no_rotation: bool = False,
    seed: Optional[int] = None,
) -> bool:
    """Головна функція обробки одного відео.
    Повертає True при успіху, False при помилці."""

    # ── Перевірка вхідного файлу ──────────────────────────────────────────────
    if not input_path.exists():
        print(f"❌ Файл не знайдено: {input_path}")
        return False

    if input_path.suffix.lower() not in VIDEO_EXTENSIONS:
        print(f"❌ Непідтримуваний формат: {input_path.suffix}")
        return False

    # ── Визначення вихідного шляху ────────────────────────────────────────────
    if output_path is None:
        output_path = input_path.parent / f"{input_path.stem}_unique.mp4"

    # ── Перевірка місця на диску (груба оцінка) ───────────────────────────────
    try:
        import shutil
        input_size = input_path.stat().st_size
        free_space = shutil.disk_usage(output_path.parent).free
        if free_space < input_size:
            print("❌ Недостатньо місця на диску")
            return False
    except OSError:
        input_size = 0

    # ── Інформація про відео через ffprobe ────────────────────────────────────
    video_info = get_video_info(input_path)
    if video_info is None:
        print(f"❌ Не вдалося прочитати як відео: {input_path}")
        return False

    streams = video_info.get('streams', [])
    video_stream = next((s for s in streams if s.get('codec_type') == 'video'), None)

    if video_stream is None:
        print(f"❌ Не знайдено відео-стрім у файлі: {input_path}")
        return False

    width = int(video_stream.get('width', 0))
    height = int(video_stream.get('height', 0))

    duration = 0.0
    try:
        duration = float(video_info.get('format', {}).get('duration', 0))
    except (ValueError, TypeError):
        pass

    dur_str = time.strftime('%M:%S', time.gmtime(int(duration))) if duration > 0 else '??:??'

    # ── Генерація параметрів ──────────────────────────────────────────────────
    params = get_random_params(preset_name, seed)

    display_seed = seed if seed is not None else params.get('noise_seed', 0)
    seed_str = f"{display_seed:06x}" if isinstance(display_seed, int) else str(display_seed)

    # ── Вивід заголовку ───────────────────────────────────────────────────────
    print(f"📂 Вхід:     {input_path.name} ({format_size(input_size)}, {width}x{height}, {dur_str})")
    print(f"🎛\ufe0f  Пресет:   {preset_name}")
    print(f"🎲 Seed:     {seed_str}")
    print()

    print_params(params, verbose)

    # ── Побудова FFmpeg команди ───────────────────────────────────────────────
    cmd = build_ffmpeg_command(
        input_path, output_path, params, video_info,
        keep_audio=keep_audio, no_rotation=no_rotation,
    )

    if verbose or dry_run:
        print("🔧 FFmpeg команда:")
        parts = []
        for a in cmd:
            parts.append(f'"{a}"' if (' ' in a or ':' in a) else a)
        # Розбиваємо довгу команду на рядки по 80 символів для читабельності
        line = '   '
        for part in parts:
            if len(line) + len(part) + 1 > 120:
                print(line + ' \\')
                line = '     ' + part
            else:
                line += (' ' if line.strip() else '') + part
        if line.strip():
            print(line)
        print()

    if dry_run:
        print("ℹ\ufe0f  Режим --dry-run: команда не виконується")
        return True

    # ── MD5 до обробки ────────────────────────────────────────────────────────
    hash_before = ''
    if show_hash:
        print("🔑 Обчислення MD5...", end='\r', flush=True)
        hash_before = file_md5(input_path)
        print(f"🔑 MD5 до:   {hash_before[:32]}...")

    # ── Запуск FFmpeg ─────────────────────────────────────────────────────────
    print("⏳ Обробка...")
    start_time = time.time()
    exit_code = run_ffmpeg(cmd, duration, verbose)
    elapsed = time.time() - start_time

    # ── Перевірка результату ──────────────────────────────────────────────────
    if exit_code != 0:
        print(f"\n❌ FFmpeg завершився з помилкою (код {exit_code}). Спробуй інший пресет.")
        if output_path.exists() and output_path.stat().st_size == 0:
            output_path.unlink()
        return False

    if not output_path.exists():
        print("❌ Вихідний файл не створено")
        return False

    output_size = output_path.stat().st_size
    if output_size == 0:
        print("❌ Вихідний файл порожній — щось пішло не так")
        output_path.unlink()
        return False

    # ── Вивід статистики ──────────────────────────────────────────────────────
    print(f"\n✅ Готово!")
    print(f"📂 Вихід:    {output_path.name} ({format_size(output_size)})")

    size_diff = output_size - input_size
    size_pct = (size_diff / input_size * 100) if input_size > 0 else 0
    sign = '+' if size_diff >= 0 else ''
    print(f"📊 Розмір:   {format_size(input_size)} \u2192 {format_size(output_size)} ({sign}{size_pct:.1f}%)")

    if show_hash:
        hash_after = file_md5(output_path)
        print(f"🔑 MD5 до:   {hash_before}")
        print(f"🔑 MD5 після: {hash_after}")

    print(f"⏱\ufe0f  Час:      {format_duration(elapsed)}")

    return True


# ─── Batch обробка ─────────────────────────────────────────────────────────────

def batch_process(
    input_dir: Path,
    output_dir: Optional[Path],
    preset_name: str,
    verbose: bool,
    show_hash: bool,
    dry_run: bool,
    keep_audio: bool,
    no_rotation: bool,
    seed: Optional[int],
) -> None:
    """Batch обробка всіх відео в папці з рекурсивним пошуком."""

    # Рекурсивний пошук відео-файлів (підтримка обох регістрів розширень)
    video_files = []
    for ext in VIDEO_EXTENSIONS:
        video_files.extend(input_dir.rglob(f'*{ext}'))
        video_files.extend(input_dir.rglob(f'*{ext.upper()}'))

    # Прибираємо дублікати і сортуємо за іменем
    video_files = sorted(set(video_files), key=lambda p: p.name.lower())

    if not video_files:
        print(f"⚠\ufe0f  Відео файли не знайдено в {input_dir}")
        return

    total = len(video_files)
    print(f"📁 Папка:    {input_dir} (знайдено {total} відео)")
    print(f"🎛\ufe0f  Пресет:   {preset_name}")
    print()

    # Статистика
    success_count = 0
    error_count = 0
    total_input_size = 0
    total_output_size = 0
    errors: List[Tuple[str, str]] = []
    batch_start = time.time()

    for idx, video_path in enumerate(video_files, 1):
        # Вихідний шлях
        if output_dir:
            output_dir.mkdir(parents=True, exist_ok=True)
            out_path = output_dir / f"{video_path.stem}_unique.mp4"
        else:
            out_path = video_path.parent / f"{video_path.stem}_unique.mp4"

        try:
            input_size = video_path.stat().st_size
        except OSError:
            print(f"[{idx}/{total}] 🎬 {video_path.name} → ❌ Помилка доступу до файлу")
            error_count += 1
            errors.append((video_path.name, "Помилка доступу до файлу"))
            continue

        total_input_size += input_size
        print(f"[{idx}/{total}] 🎬 {video_path.name} ({format_size(input_size)}) → ", end='', flush=True)

        # Кожен файл отримує унікальний seed (якщо seed задано)
        file_seed = (seed + idx) if seed is not None else None

        # Читаємо інформацію про відео
        video_info = get_video_info(video_path)
        if video_info is None:
            print("❌ Помилка: corrupted file")
            error_count += 1
            errors.append((video_path.name, "corrupted file"))
            continue

        params = get_random_params(preset_name, file_seed)
        cmd = build_ffmpeg_command(
            video_path, out_path, params, video_info,
            keep_audio=keep_audio, no_rotation=no_rotation,
        )

        if dry_run:
            print(f"🔍 (dry-run) {out_path.name}")
            success_count += 1
            continue

        file_start = time.time()
        exit_code = run_ffmpeg_silent(cmd)
        file_elapsed = time.time() - file_start

        if exit_code != 0:
            print(f"❌ Помилка: FFmpeg код {exit_code}")
            error_count += 1
            errors.append((video_path.name, f"FFmpeg завершився з помилкою (код {exit_code})"))
            if out_path.exists() and out_path.stat().st_size == 0:
                out_path.unlink()
            continue

        if not out_path.exists() or out_path.stat().st_size == 0:
            print("❌ Вихідний файл порожній")
            error_count += 1
            errors.append((video_path.name, "Вихідний файл порожній"))
            if out_path.exists():
                out_path.unlink()
            continue

        output_size = out_path.stat().st_size
        total_output_size += output_size
        success_count += 1
        print(f"✅ {out_path.name} ({format_size(output_size)}) [{format_duration(file_elapsed)}]")

    # Підсумок
    total_elapsed = time.time() - batch_start
    print()
    print("══════════════════════════════════════════")
    print("📊 Підсумок:")
    print(f"   Оброблено:  {success_count}/{total} файлів")
    if error_count > 0:
        print(f"   Помилки:    {error_count}")
        for fname, err in errors:
            print(f"     • {fname}: {err}")
    print(f"   Розмір:     {format_size(total_input_size)} \u2192 {format_size(total_output_size)}")
    print(f"   Загальний час: {format_duration(total_elapsed)}")
    print("══════════════════════════════════════════")


# ─── Точка входу ──────────────────────────────────────────────────────────────

def main() -> None:
    """Точка входу: argparse → check_ffmpeg → single/batch обробка."""
    print_banner()

    parser = argparse.ArgumentParser(
        prog='uniquifier.py',
        description='Video Uniquifier v1.0 — Змінює цифровий відбиток відео без видимих змін',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Приклади:
  python uniquifier.py video.mp4
  python uniquifier.py video.mp4 -o ready.mp4 -p hard
  python uniquifier.py video.mp4 --preset light --show-hash --verbose
  python uniquifier.py ./downloads/ -o ./ready/ -p medium
  python uniquifier.py video.mp4 --dry-run
  python uniquifier.py video.mp4 --keep-audio --no-rotation
  python uniquifier.py video.mp4 --seed 42
        """,
    )

    parser.add_argument(
        'input',
        help='Шлях до відео файлу або папки з відео',
    )
    parser.add_argument(
        '-o', '--output',
        help='Шлях для вихідного файлу або папки (за замовчуванням: input_unique.mp4)',
    )
    parser.add_argument(
        '-p', '--preset',
        choices=['stealth', 'light', 'medium', 'hard'],
        default='medium',
        help='Пресет унікалізації (за замовчуванням: medium)',
    )
    parser.add_argument(
        '--verbose',
        action='store_true',
        help='Детальний вивід (показати FFmpeg команду і всі параметри)',
    )
    parser.add_argument(
        '--show-hash',
        action='store_true',
        help='Показати MD5 хеш файлу до і після обробки',
    )
    parser.add_argument(
        '--dry-run',
        action='store_true',
        help='Тільки показати FFmpeg команду, не виконувати',
    )
    parser.add_argument(
        '--keep-audio',
        action='store_true',
        help='Не змінювати аудіо (тільки відео-фільтри)',
    )
    parser.add_argument(
        '--no-rotation',
        action='store_true',
        help='Вимкнути мікро-поворот (якщо помітний на конкретному відео)',
    )
    parser.add_argument(
        '--seed',
        type=int,
        default=None,
        help='Фіксований seed для рандому (для відтворюваності результатів)',
    )

    args = parser.parse_args()

    check_ffmpeg()

    input_path = Path(args.input)
    output_path = Path(args.output) if args.output else None

    if input_path.is_dir():
        batch_process(
            input_dir=input_path,
            output_dir=output_path,
            preset_name=args.preset,
            verbose=args.verbose,
            show_hash=args.show_hash,
            dry_run=args.dry_run,
            keep_audio=args.keep_audio,
            no_rotation=args.no_rotation,
            seed=args.seed,
        )
    elif input_path.is_file():
        success = uniquify_video(
            input_path=input_path,
            output_path=output_path,
            preset_name=args.preset,
            verbose=args.verbose,
            show_hash=args.show_hash,
            dry_run=args.dry_run,
            keep_audio=args.keep_audio,
            no_rotation=args.no_rotation,
            seed=args.seed,
        )
        sys.exit(0 if success else 1)
    else:
        print(f"❌ Файл не знайдено: {input_path}")
        sys.exit(1)


if __name__ == '__main__':
    main()
