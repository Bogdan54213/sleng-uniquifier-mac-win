#!/usr/bin/env python3
# -*- coding: utf-8 -*-
import json
import threading
import urllib.request

try:
    from config import BOT_TOKEN, ADMIN_CHAT_ID
except ImportError:
    BOT_TOKEN     = ""
    ADMIN_CHAT_ID = ""


def _send(text: str):
    if not BOT_TOKEN or not ADMIN_CHAT_ID:
        return
    url     = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    payload = json.dumps({
        "chat_id":    ADMIN_CHAT_ID,
        "text":       text,
        "parse_mode": "HTML",
    }).encode('utf-8')
    req = urllib.request.Request(
        url, data=payload,
        headers={"Content-Type": "application/json; charset=utf-8"},
    )
    try:
        with urllib.request.urlopen(req, timeout=10):
            pass
    except Exception:
        pass


def notify_registration(name: str, contact: str, machine_id: str):
    text = (
        "📝 <b>Новий запит на доступ до Sleng Унікалізатор</b>\n\n"
        f"👤 Ім'я: <b>{name}</b>\n"
        f"📱 Контакт: {contact or '—'}\n"
        f"🔑 Machine ID: <code>{machine_id}</code>\n\n"
        "Відкрий адмін-панель у своєму додатку (<b>Ctrl+Shift+A</b>),\n"
        "введи Machine ID і натисни <b>Генерувати код</b>.\n"
        "Відправ отриманий код учню."
    )
    threading.Thread(target=_send, args=(text,), daemon=True).start()
