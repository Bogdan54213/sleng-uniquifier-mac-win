#!/usr/bin/env python3
# -*- coding: utf-8 -*-
import hashlib
import hmac
import os
import sqlite3
import subprocess
import sys
from pathlib import Path

try:
    from config import HMAC_SECRET, ADMIN_PASSWORD_HASH
except ImportError:
    # Dev fallback. SHA256('sleng2024') — для запуску без config.py.
    HMAC_SECRET = "CHANGE_THIS_SECRET_32CHARS!!"
    ADMIN_PASSWORD_HASH = "c8a7c7be0e5f47f7fc25d3f06be6e1f9b5bce67c1e3f76dd6a0e3b3c4ae6e8c5"


def _hash_password(plain: str) -> str:
    """SHA-256 hex digest. Використовується для перевірки admin password
    без зберігання plaintext'а в config.py / .exe бандлі.
    """
    return hashlib.sha256((plain or '').encode('utf-8')).hexdigest()


# ── База даних ────────────────────────────────────────────────────────────────

def get_db_path() -> Path:
    base = os.environ.get('LOCALAPPDATA') or os.environ.get('APPDATA') or str(Path.home())
    d = Path(base) / 'SlengUniquifier'
    d.mkdir(parents=True, exist_ok=True)
    return d / 'auth.db'


def _conn():
    return sqlite3.connect(str(get_db_path()))


def init_db():
    with _conn() as c:
        c.execute('''CREATE TABLE IF NOT EXISTS auth (
            id         INTEGER PRIMARY KEY,
            machine_id TEXT NOT NULL,
            name       TEXT,
            contact    TEXT,
            status     TEXT DEFAULT 'pending',
            code       TEXT,
            created_at TEXT DEFAULT (datetime('now'))
        )''')


# ── Machine ID ────────────────────────────────────────────────────────────────
#
# СТАРИЙ підхід використовував `wmic baseboard get serialnumber` + diskdrive,
# але:
#   1. У Windows 11 24H2 wmic.exe ВИДАЛИЛИ — на нових ПК це падало з
#      FileNotFoundError, sub.check_output повертало порожнечу → fallback
#      на uuid.getnode() який може давати дублікати на схожих машинах.
#   2. Виклик wmic займає 1-2 секунди при кожному отриманні machine_id.
#
# НОВИЙ підхід: один раз генеруємо стійкий UUID v4 і зберігаємо його у файл
# поряд з auth.db. Файл живе у %LOCALAPPDATA%\SlengUniquifier\machine_id
# — переживає всі апдейти/перевстановлення додатку.
#
# Якщо студент перевстановлює Windows або міняє диск — machine_id оновиться,
# і йому треба буде запитати новий код. Це нормальна поведінка.

import uuid as _uuid


def _machine_id_file() -> Path:
    return get_db_path().parent / 'machine_id'


def get_machine_id() -> str:
    """
    Стійкий unique-ID цієї машини у форматі XXXX-XXXX-XXXX-XXXX.

    Генерується ОДИН раз при першому виклику і зберігається у файл.
    Подальші виклики просто читають з файлу — ~миттєво.
    """
    mid_file = _machine_id_file()

    # 1. Спроба прочитати існуючий
    try:
        if mid_file.exists():
            stored = mid_file.read_text(encoding='utf-8').strip()
            if _is_valid_machine_id(stored):
                return stored
    except Exception:
        pass

    # 2. Генеруємо новий, спробуючи прив'язати до системних даних для стабільності
    try:
        # На Windows беремо реальний MachineGuid з реєстру — це системний
        # ID який не змінюється до перевстановлення Windows. Якщо доступний —
        # ідеально (детермінований і стійкий).
        if sys.platform == 'win32':
            try:
                import winreg
                with winreg.OpenKey(
                    winreg.HKEY_LOCAL_MACHINE,
                    r"SOFTWARE\Microsoft\Cryptography",
                    0,
                    winreg.KEY_READ | winreg.KEY_WOW64_64KEY,
                ) as k:
                    guid, _ = winreg.QueryValueEx(k, "MachineGuid")
                    h = hashlib.sha256(guid.encode()).hexdigest()[:16].upper()
                    mid = f"{h[:4]}-{h[4:8]}-{h[8:12]}-{h[12:16]}"
                    try:
                        mid_file.write_text(mid, encoding='utf-8')
                    except OSError:
                        pass
                    return mid
            except (ImportError, FileNotFoundError, OSError):
                pass

        # На Mac/Linux або якщо реєстр недоступний — використовуємо випадковий
        # UUID4 (зберігається у файл, тож стабільний для цього інсталу).
        raw = _uuid.uuid4().hex
        h = hashlib.sha256(raw.encode()).hexdigest()[:16].upper()
        mid = f"{h[:4]}-{h[4:8]}-{h[8:12]}-{h[12:16]}"
        try:
            mid_file.write_text(mid, encoding='utf-8')
        except OSError:
            pass
        return mid
    except Exception:
        # Найгірший випадок — фолбек на mac-адресу. Хоча б щось.
        h = hashlib.sha256(str(_uuid.getnode()).encode()).hexdigest()[:16].upper()
        return f"{h[:4]}-{h[4:8]}-{h[8:12]}-{h[12:16]}"


def _is_valid_machine_id(s: str) -> bool:
    """XXXX-XXXX-XXXX-XXXX, hex chars, 19 chars."""
    if not s or len(s) != 19:
        return False
    parts = s.split('-')
    if len(parts) != 4:
        return False
    return all(len(p) == 4 and all(c in '0123456789ABCDEF' for c in p.upper()) for p in parts)


# ── Реєстрація ────────────────────────────────────────────────────────────────

def get_registration():
    """Повертає (id, machine_id, name, contact, status, code, created_at) або None."""
    init_db()
    with _conn() as c:
        return c.execute(
            'SELECT id, machine_id, name, contact, status, code, created_at '
            'FROM auth ORDER BY id DESC LIMIT 1'
        ).fetchone()


def save_registration(name: str, contact: str):
    init_db()
    mid = get_machine_id()
    with _conn() as c:
        c.execute('DELETE FROM auth')
        c.execute(
            'INSERT INTO auth (machine_id, name, contact, status) VALUES (?,?,?,?)',
            (mid, name, contact, 'pending')
        )


def is_activated() -> bool:
    reg = get_registration()
    return reg is not None and reg[4] == 'active'


# ── Коди активації ────────────────────────────────────────────────────────────

def _normalize(code: str) -> str:
    return code.upper().replace('-', '').replace(' ', '')


def generate_activation_code(machine_id: str) -> str:
    """HMAC-SHA256 від machine_id. Детермінований — один machine_id = один код."""
    clean = _normalize(machine_id)
    h = hmac.new(HMAC_SECRET.encode(), clean.encode(), hashlib.sha256).hexdigest()
    raw = h[:12].upper()
    return f"{raw[:4]}-{raw[4:8]}-{raw[8:12]}"


def validate_and_activate(code: str) -> bool:
    """Перевіряє код і активує якщо вірний. Повертає True при успіху."""
    init_db()
    reg = get_registration()

    # Адмін-пароль як майстер-код (активує на будь-якій машині).
    # Порівнюємо хеш — щоб у .exe не лежав plaintext.
    if _hash_password(code.strip()) == ADMIN_PASSWORD_HASH:
        if not reg:
            mid = get_machine_id()
            with _conn() as c:
                c.execute(
                    'INSERT INTO auth (machine_id, name, contact, status, code) VALUES (?,?,?,?,?)',
                    (mid, 'Admin', '', 'active', code)
                )
        else:
            with _conn() as c:
                c.execute('UPDATE auth SET status=?, code=? WHERE id=?',
                          ('active', code, reg[0]))
        return True

    if not reg:
        return False

    expected = generate_activation_code(reg[1])
    if _normalize(code) == _normalize(expected):
        with _conn() as c:
            c.execute('UPDATE auth SET status=?, code=? WHERE id=?',
                      ('active', code, reg[0]))
        return True

    return False


def check_admin_password(password: str) -> bool:
    # Не зберігаємо plaintext — порівнюємо SHA-256 хеші через
    # constant-time compare (захист від timing-attacks).
    return hmac.compare_digest(_hash_password(password), ADMIN_PASSWORD_HASH)
