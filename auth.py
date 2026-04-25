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
    from config import HMAC_SECRET, ADMIN_PASSWORD
except ImportError:
    HMAC_SECRET    = "CHANGE_THIS_SECRET_32CHARS!!"
    ADMIN_PASSWORD = "sleng2024"


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

def get_machine_id() -> str:
    try:
        if sys.platform == 'win32':
            mb = subprocess.check_output(
                'wmic baseboard get serialnumber',
                shell=True, stderr=subprocess.DEVNULL
            ).decode(errors='ignore').split('\n')
            mb = ''.join(mb[1:]).strip()

            disk = subprocess.check_output(
                'wmic diskdrive get serialnumber',
                shell=True, stderr=subprocess.DEVNULL
            ).decode(errors='ignore').split('\n')
            disk = ''.join(disk[1:]).strip()

            raw = f"{mb}:{disk}"
            if raw.strip(':'):
                h = hashlib.sha256(raw.encode()).hexdigest()[:16].upper()
                return f"{h[:4]}-{h[4:8]}-{h[8:12]}-{h[12:16]}"
    except Exception:
        pass

    import uuid
    h = hashlib.sha256(str(uuid.getnode()).encode()).hexdigest()[:16].upper()
    return f"{h[:4]}-{h[4:8]}-{h[8:12]}-{h[12:16]}"


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

    # Адмін-пароль як майстер-код (активує на будь-якій машині)
    if code.strip() == ADMIN_PASSWORD:
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
    return password == ADMIN_PASSWORD
