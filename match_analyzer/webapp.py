"""Сборка веб-приложения: статические файлы из web/ + свежий data.json."""
from __future__ import annotations

import json
import math
import shutil
from pathlib import Path

import pandas as pd

from . import data
from .service import analyze_fixtures, load_strategy
from .strategy import LABEL, MARKET_OF

WEB_DIR = Path(__file__).resolve().parent.parent / "web"


def _num(x, nd=4):
    try:
        x = float(x)
    except (TypeError, ValueError):
        return None
    return None if math.isnan(x) else round(x, nd)


def _moscow(date: pd.Timestamp, time: str) -> tuple[str, str]:
    """football-data указывает время по Великобритании — переводим в московское."""
    try:
        hh, mm = (int(x) for x in str(time).split(":")[:2])
    except ValueError:
        return f"{date:%Y-%m-%d}", ""
    ts = pd.Timestamp(date.year, date.month, date.day, hh, mm, tz="Europe/London").tz_convert("Europe/Moscow")
    return f"{ts:%Y-%m-%d}", f"{ts:%H:%M}"


def build_payload(sel: pd.DataFrame, strategy, cfg: dict, now: pd.Timestamp | None = None) -> dict:
    now = now or pd.Timestamp.now(tz="Europe/Moscow")
    bets = strategy.select(sel) if not sel.empty else pd.DataFrame()
    bet_key = {}
    for _, b in bets.iterrows():
        bet_key[(b["date"], b["home"], b["away"])] = {
            "selection": b["selection"], "label": LABEL[b["selection"]], "market": MARKET_OF[b["selection"]],
            "price": _num(b["price"], 2), "p": _num(b["p"]), "market_p": _num(b["market_p"]),
            "edge": _num(b["edge"]), "stake": _num(b["stake_frac"]),
        }
    matches = []
    if not sel.empty:
        for (date, home, away), g in sel.groupby(["date", "home", "away"], sort=False):
            g = g.set_index("selection")
            first = g.iloc[0]
            day, time = _moscow(date, first["time"] if "time" in g.columns else "")
            mw = strategy.market_weight
            blend = {s: _num((1 - mw) * g.at[s, "model_p"] + mw * (g.at[s, "market_p"]
                             if pd.notna(g.at[s, "market_p"]) else g.at[s, "model_p"])) for s in g.index}
            matches.append({
                "date": day,
                "time": time,
                "p": blend,
                "league": first["league"],
                "home": home, "away": away,
                "xg": [_num(first.get("xg_home"), 2), _num(first.get("xg_away"), 2)],
                "model": {s: _num(g.at[s, "model_p"]) for s in g.index},
                "market": {s: _num(g.at[s, "market_p"]) for s in g.index},
                "odds": {s: _num(g.at[s, "price"], 2) for s in g.index},
                "bet": bet_key.get((date, home, away)),
            })
    matches.sort(key=lambda m: (m["date"], m["time"], m["league"], m["home"]))
    return {
        "updated": now.isoformat(timespec="minutes"),
        "leagues": data.LEAGUES,
        "strategy": cfg,
        "matches": matches,
    }


def build_site(out_dir: str | Path, leagues: list[str], strategy_file: str | None,
               history: pd.DataFrame | None = None, fixtures: pd.DataFrame | None = None,
               seasons: int = 3) -> dict:
    strategy, cfg = load_strategy(strategy_file)
    if history is None:
        history = data.load_history(leagues, data.previous_seasons(seasons), verbose=False)
    if fixtures is None:
        fixtures = data.load_fixtures(leagues)
    sel = analyze_fixtures(history, fixtures) if not fixtures.empty else pd.DataFrame()
    payload = build_payload(sel, strategy, cfg)
    out = Path(out_dir)
    if out.exists():
        shutil.rmtree(out)
    shutil.copytree(WEB_DIR, out)
    (out / "data.json").write_text(json.dumps(payload, ensure_ascii=False, separators=(",", ":")))
    return payload
