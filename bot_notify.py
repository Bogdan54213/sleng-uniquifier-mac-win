#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Інформує адміна у Telegram про новий запит на доступ до Уніфікатора.

V2 (з inline-кнопками):
  При реєстрації шлемо адміну повідомлення з 2 кнопками:
    [✅ Видати код]  [❌ Відхилити]
  Натиснувши «✅ Видати код», адмін одразу отримує згенерований код
  у відповідь — без потреби відкривати Ctrl+Shift+A панель.

Старий формат без кнопок підтримується (якщо за якихось причин
кнопкові callback'и не працюватимуть, адмін може сам згенерувати
код через Ctrl+Shift+A у додатку).
"""
import json
import threading
import urllib.parse
import urllib.request

try:
    from config import BOT_TOKEN, ADMIN_CHAT_ID
except ImportError:
    BOT_TOKEN     = ""
    ADMIN_CHAT_ID = ""


def _telegram_api(method: str, payload: dict) -> None:
    """Тихо викликаємо Telegram Bot API. У разі помилки — мовчки пропускаємо."""
    if not BOT_TOKEN or not ADMIN_CHAT_ID:
        return
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/{method}"
    data = json.dumps(payload).encode('utf-8')
    req = urllib.request.Request(
        url, data=data,
        headers={"Content-Type": "application/json; charset=utf-8"},
    )
    try:
        with urllib.request.urlopen(req, timeout=10):
            pass
    except Exception:
        pass


def _send(text: str, reply_markup: dict | None = None) -> None:
    payload = {
        "chat_id":    ADMIN_CHAT_ID,
        "text":       text,
        "parse_mode": "HTML",
    }
    if reply_markup is not None:
        payload["reply_markup"] = reply_markup
    _telegram_api("sendMessage", payload)


def notify_registration(name: str, contact: str, machine_id: str) -> None:
    """
    Шлемо адміну запит з кнопками. Callback_data — це сам machine_id
    префіксований action.

    Кнопки:
      uniq_grant:<machine_id>    — згенерувати і прислати код
      uniq_deny:<machine_id>     — відхилити (просто видалити повідомлення)
    """
    text = (
        "📝 <b>Новий запит на доступ — Sleng Унікалізатор</b>\n\n"
        f"👤 Ім'я: <b>{name}</b>\n"
        f"📱 Контакт: {contact or '—'}\n"
        f"🔑 Machine ID: <code>{machine_id}</code>\n\n"
        "Тицяй кнопку нижче — бот відразу видасть код."
    )

    # Telegram-обмеження: callback_data ≤ 64 байти.
    # Machine ID = 19 символів (XXXX-XXXX-XXXX-XXXX). 19 + 10 префіксу — OK.
    reply_markup = {
        "inline_keyboard": [[
            {
                "text": "✅ Видати код",
                "callback_data": f"uniq_grant:{machine_id}",
            },
            {
                "text": "❌ Відхилити",
                "callback_data": f"uniq_deny:{machine_id}",
            },
        ]]
    }

    threading.Thread(
        target=_send,
        args=(text, reply_markup),
        daemon=True,
    ).start()
