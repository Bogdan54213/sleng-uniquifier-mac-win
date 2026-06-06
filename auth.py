#!/usr/bin/env python3
# -*- coding: utf-8 -*-
import hashlib
import hmac
import os
import sqlite3
import subprocess
import sys
from pathlib import Path
from typing import Optional, Tuple

try:
    from config import HMAC_SECRET, ADMIN_PASSWORD_HASH
except ImportError:
    # Dev fallback. SHA256('sleng2024') — для запуску без config.py.
    HMAC_SECRET = "CHANGE_THIS_SECRET_32CHARS!!"
    ADMIN_PASSWORD_HASH = "c8a7c7be0e5f47f7fc25d3f06be6e1f9b5bce67c1e3f76dd6a0e3b3c4ae6e8c5"

# URL Telegram-бота — auth-server для видачі JWT з роллю.
# На production буде Railway public URL. Локально для тестів — localhost:8080.
try:
    from config import BOT_AUTH_URL
except ImportError:
    BOT_AUTH_URL = "http://localhost:8080"


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
        # JWT з бота — зберігаємо токен + роль + час оновлення.
        # Додаємо ALTER через try, щоб не падало на старій БД де колонок ще нема.
        for ddl in (
            "ALTER TABLE auth ADD COLUMN jwt_token TEXT",
            "ALTER TABLE auth ADD COLUMN jwt_role TEXT",
            "ALTER TABLE auth ADD COLUMN jwt_updated_at TEXT",
        ):
            try:
                c.execute(ddl)
            except sqlite3.OperationalError:
                pass  # колонка вже існує


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


def _fetch_jwt_from_bot(machine_id: str, code: str) -> Optional[tuple[str, str]]:
    """Звертається до bot HTTP API /api/sleng/auth для отримання JWT.

    Повертає (token, role) при успіху, None при будь-якій помилці.
    НЕ блокує активацію якщо бот недоступний — це опціональний enrichment.
    Таймаут жорсткий (5с) — не хочемо вішати UX через повільну мережу.
    """
    import json
    import urllib.request
    import urllib.error

    try:
        payload = json.dumps({"machine_id": machine_id, "code": code}).encode("utf-8")
        req = urllib.request.Request(
            f"{BOT_AUTH_URL}/api/sleng/auth",
            data=payload,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=5) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            token = data.get("token")
            role = data.get("role")
            if token and role:
                return token, role
    except urllib.error.HTTPError as e:
        # 403 = invalid_code / no_activation_record / blocked. Це не наша проблема —
        # локальний HMAC уже пройшов. Логуємо і продовжуємо без JWT.
        try:
            body = e.read().decode("utf-8")
        except Exception:
            body = str(e)
        print(f"[auth] bot rejected: HTTP {e.code} {body}")
    except Exception as e:
        # Бот offline / DNS fail / timeout — нічого страшного, fallback на legacy
        print(f"[auth] bot unreachable: {type(e).__name__}: {e}")
    return None


def _save_jwt(reg_id: int, token: str, role: str) -> None:
    """Зберігає JWT у БД для подальших admin-перевірок."""
    with _conn() as c:
        c.execute(
            "UPDATE auth SET jwt_token=?, jwt_role=?, jwt_updated_at=datetime('now') "
            "WHERE id=?",
            (token, role, reg_id),
        )


def get_jwt() -> Optional[tuple[str, str]]:
    """Повертає (token, role) поточного користувача або None.

    Не валідує signature тут — це робить bot/server.py через verify.
    """
    with _conn() as c:
        row = c.execute(
            "SELECT jwt_token, jwt_role FROM auth WHERE jwt_token IS NOT NULL "
            "ORDER BY id DESC LIMIT 1"
        ).fetchone()
    if row and row[0]:
        return row[0], row[1] or "STUDENT"
    return None


def is_admin() -> bool:
    """True якщо JWT.role належить до SUPER_ADMIN/CURATOR."""
    jwt = get_jwt()
    if not jwt:
        return False
    _, role = jwt
    return role in ("SUPER_ADMIN", "CURATOR")


def validate_and_activate(code: str) -> bool:
    """Перевіряє код і активує якщо вірний. Повертає True при успіху.

    Після успішної локальної валідації — фоном запитує JWT з бота
    і зберігає його разом з роллю. Якщо бот недоступний — активація
    все одно проходить, але без role (admin-доступу не буде).
    """
    init_db()
    reg = get_registration()

    # ДІАГНОСТИКА — логуємо що Sleng реально обчислює.
    # Хеш HMAC_SECRET (не plaintext!) видно у server.log щоб порівняти з очікуваним.
    secret_fp = hashlib.sha256(HMAC_SECRET.encode()).hexdigest()[:12]
    print(f"[auth] validate_and_activate: input_code='{code}', "
          f"HMAC_SECRET_fp={secret_fp}, len={len(HMAC_SECRET)}")
    if reg:
        expected = generate_activation_code(reg[1])
        print(f"[auth] machine_id_in_db='{reg[1]}', "
              f"expected_code='{expected}', input_normalized='{_normalize(code)}'")
    else:
        print("[auth] no registration in DB — only master-password works")

    # Адмін-пароль як майстер-код (активує на будь-якій машині).
    # Порівнюємо хеш — щоб у .exe не лежав plaintext.
    if _hash_password(code.strip()) == ADMIN_PASSWORD_HASH:
        if not reg:
            mid = get_machine_id()
            with _conn() as c:
                cursor = c.execute(
                    'INSERT INTO auth (machine_id, name, contact, status, code) VALUES (?,?,?,?,?)',
                    (mid, 'Admin', '', 'active', code)
                )
                reg_id = cursor.lastrowid
        else:
            reg_id = reg[0]
            with _conn() as c:
                c.execute('UPDATE auth SET status=?, code=? WHERE id=?',
                          ('active', code, reg_id))
        # Master-password — локально вже сам по собі admin, JWT не критичний
        return True

    if not reg:
        return False

    expected = generate_activation_code(reg[1])
    if _normalize(code) == _normalize(expected):
        with _conn() as c:
            c.execute('UPDATE auth SET status=?, code=? WHERE id=?',
                      ('active', code, reg[0]))
        # Якщо бот доступний — отримуємо JWT з роллю
        jwt_pair = _fetch_jwt_from_bot(reg[1], code)
        if jwt_pair:
            token, role = jwt_pair
            _save_jwt(reg[0], token, role)
            print(f"[auth] JWT acquired (role={role})")
        return True

    return False


def check_admin_password(password: str) -> bool:
    # Не зберігаємо plaintext — порівнюємо SHA-256 хеші через
    # constant-time compare (захист від timing-attacks).
    return hmac.compare_digest(_hash_password(password), ADMIN_PASSWORD_HASH)
