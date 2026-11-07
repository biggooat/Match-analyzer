"""Минимальный клиент Telegram Bot API (без сторонних библиотек)."""
from __future__ import annotations

import json
import time
import traceback
import urllib.parse
import urllib.request

from . import data
from .service import fetch_upcoming, load_strategy, tips_message

LIMIT = 4000


def api(token: str, method: str, **params):
    body = urllib.parse.urlencode({k: v for k, v in params.items() if v is not None}).encode()
    req = urllib.request.Request(f"https://api.telegram.org/bot{token}/{method}", data=body)
    timeout = int(params.get("timeout", 0)) + 30
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        payload = json.loads(resp.read())
    if not payload.get("ok"):
        raise RuntimeError(f"Telegram: {payload}")
    return payload["result"]


def split_message(text: str, limit: int = LIMIT) -> list[str]:
    """Режет длинный текст по строкам, чтобы не превысить лимит Telegram."""
    parts, cur = [], ""
    for line in text.split("\n"):
        while len(line) > limit:
            parts.append(line[:limit])
            line = line[limit:]
        if len(cur) + len(line) + 1 > limit:
            parts.append(cur)
            cur = ""
        cur = f"{cur}\n{line}" if cur else line
    if cur:
        parts.append(cur)
    return parts


def send(token: str, chat_id: str | int, text: str) -> None:
    for chunk in split_message(text):
        api(token, "sendMessage", chat_id=chat_id, text=chunk, parse_mode="HTML",
            disable_web_page_preview="true")


HELP = (
    "⚽ <b>Match Analyzer</b>\n\n"
    "/tips — ставки на ближайшие матчи (лиги по умолчанию)\n"
    "/tips E0 D1 — ставки по указанным лигам\n"
    "/leagues — коды лиг\n"
    "/id — ваш chat id (для рассылки через GitHub)\n\n"
    "Бот ищет value-ставки: где коэффициент выше реальной вероятности исхода."
)


def run_bot(token: str, leagues: list[str], strategy_file: str | None = None,
            allowed: set[str] | None = None) -> None:
    """Интерактивный бот (long polling). Нужен постоянно работающий сервер."""
    print("Бот запущен. Ctrl+C — остановка.")
    offset = None
    while True:
        try:
            updates = api(token, "getUpdates", offset=offset, timeout=50)
        except Exception as exc:
            print(f"getUpdates: {exc}")
            time.sleep(5)
            continue
        for upd in updates:
            offset = upd["update_id"] + 1
            msg = upd.get("message") or {}
            chat = msg.get("chat", {}).get("id")
            text = (msg.get("text") or "").strip()
            if not chat or not text.startswith("/"):
                continue
            if allowed and str(chat) not in allowed:
                send(token, chat, "Доступ закрыт.")
                continue
            cmd, *rest = text.split()
            cmd = cmd.split("@")[0].lower()
            try:
                if cmd in ("/start", "/help"):
                    send(token, chat, HELP)
                elif cmd == "/id":
                    send(token, chat, f"Ваш chat id: <code>{chat}</code>")
                elif cmd == "/leagues":
                    send(token, chat, "\n".join(f"<code>{c}</code> {n}" for c, n in data.LEAGUES.items()))
                elif cmd == "/tips":
                    lg = [x.upper() for x in rest if x.upper() in data.LEAGUES] or leagues
                    send(token, chat, f"⏳ Считаю ({', '.join(lg)})…")
                    strat, cfg = load_strategy(strategy_file)
                    bets, n = fetch_upcoming(lg, strat)
                    send(token, chat, tips_message(bets, n, cfg))
                else:
                    send(token, chat, HELP)
            except Exception as exc:
                traceback.print_exc()
                send(token, chat, f"Ошибка: {exc}")
