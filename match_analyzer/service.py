"""Общие сценарии для CLI и Telegram: ставки на ближайшие матчи и отчёт по истории."""
from __future__ import annotations

import html
import json
from dataclasses import replace
from pathlib import Path

import pandas as pd

from . import data
from .backtest import optimize, simulate, summarize, walk_forward
from .model import PoissonModel
from .strategy import LABEL, Strategy, build_selections, predict_matches


def load_strategy(path: str | Path | None, base: Strategy | None = None) -> tuple[Strategy, dict]:
    """Стратегия из JSON (файл может отсутствовать — тогда настройки по умолчанию)."""
    base = base or Strategy()
    if not path or not Path(path).exists():
        return base, {}
    cfg = json.loads(Path(path).read_text())
    kw = {k: v for k, v in cfg.items() if k in base.to_dict()}
    if "markets" in kw:
        kw["markets"] = tuple(kw["markets"])
    return replace(base, **kw), cfg


def upcoming_bets(history: pd.DataFrame, fixtures: pd.DataFrame, strategy: Strategy) -> tuple[pd.DataFrame, int]:
    """Возвращает (ставки, число проанализированных матчей)."""
    out = []
    for league, fx in fixtures.groupby("league"):
        hist = history[history["league"] == league]
        if hist.dropna(subset=["hg"]).empty:
            continue
        model = PoissonModel().fit(hist, ref_date=fx["date"].min())
        sel = build_selections(fx, predict_matches(model, fx))
        sel["won"] = float("nan")
        out.append(sel)
    if not out:
        return pd.DataFrame(), 0
    sel = pd.concat(out, ignore_index=True)
    n = sel[["date", "home", "away"]].drop_duplicates().shape[0]
    return strategy.select(sel), n


def fetch_upcoming(leagues: list[str], strategy: Strategy, seasons: int = 3) -> tuple[pd.DataFrame, int]:
    history = data.load_history(leagues, data.previous_seasons(seasons), verbose=False)
    fixtures = data.load_fixtures(leagues)
    if fixtures.empty:
        return pd.DataFrame(), 0
    return upcoming_bets(history, fixtures, strategy)


def tips_message(bets: pd.DataFrame, n_matches: int, cfg: dict | None = None) -> str:
    e = html.escape
    lines = ["⚽ <b>Ставки на ближайшие матчи</b>"]
    if cfg and cfg.get("robust") is False:
        lines.append("⚠️ <i>Последняя проверка на истории не нашла надёжно прибыльной стратегии. "
                     "Ставки ниже — только для наблюдения.</i>")
    if n_matches == 0:
        lines.append("\nНет ближайших матчей с коэффициентами по выбранным лигам.")
        return "\n".join(lines)
    if bets.empty:
        lines.append(f"\nПроанализировано матчей: {n_matches}.\n"
                     "Подходящих ставок нет — лучше пропустить, чем играть без перевеса.")
        return "\n".join(lines)
    lines.append(f"Матчей проанализировано: {n_matches}, отобрано ставок: {len(bets)}\n")
    for league, g in bets.sort_values(["league", "date"]).groupby("league"):
        lines.append(f"🏆 <b>{e(data.LEAGUES.get(league, league))}</b>")
        for _, b in g.iterrows():
            lines.append(
                f"{b['date']:%d.%m} {e(b['home'])} — {e(b['away'])}\n"
                f"   👉 <b>{LABEL[b['selection']]}</b> @ {b['price']:.2f}  "
                f"(вероятность {b['p']:.0%}, перевес {b['edge']:+.0%}, ставка {b['stake_frac']:.1%} банка)")
        lines.append("")
    lines.append("<i>Кэф — лучший по рынку; у вашего букмекера он может быть ниже. "
                 "Не ставьте, если кэф упал настолько, что перевес исчез.</i>")
    return "\n".join(lines)


def weekly_report(leagues: list[str], seasons: int, out_path: str | Path,
                  matches: pd.DataFrame | None = None) -> str:
    """Бэктест + подбор стратегии; сохраняет лучшую стратегию в out_path и возвращает текст отчёта."""
    if matches is None:
        matches = data.load_history(leagues, data.previous_seasons(seasons), verbose=False)
    sel = walk_forward(matches, verbose=False)
    dates = sel["date"].sort_values()
    holdout = dates.iloc[int(len(dates) * 2 / 3)]
    res = optimize(sel, min_bets=60, holdout_from=holdout)

    lines = ["📊 <b>Еженедельная проверка стратегии</b>",
             f"Лиги: {', '.join(leagues)}; матчей в бэктесте: {sel[['date', 'home']].drop_duplicates().shape[0]}",
             f"Подбор до {holdout:%d.%m.%Y}, проверка — после.\n"]
    if res.empty:
        cfg = {"robust": False}
        lines.append("Ни одна настройка не набрала достаточно ставок.")
    else:
        res["score"] = res[["train_t_stat", "test_t_stat"]].min(axis=1)
        robust = res[(res["train_roi"] > 0) & (res["test_roi"] > 0)]
        best = (robust if not robust.empty else res).sort_values("score", ascending=False).iloc[0]
        cfg = {"markets": list(best["markets"]), "min_edge": float(best["min_edge"]),
               "min_odds": float(best["odds_range"][0]), "max_odds": float(best["odds_range"][1]),
               "market_weight": float(best["market_weight"]), "min_prob": float(best["min_prob"]),
               "robust": bool(not robust.empty and best["test_t_stat"] > 1.0),
               "updated": pd.Timestamp.now().strftime("%Y-%m-%d")}
        strat, _ = load_strategy(None)
        strat = replace(strat, **{k: (tuple(v) if k == "markets" else v)
                                  for k, v in cfg.items() if k in strat.to_dict()})
        full = summarize(simulate(sel, strat))
        lines += [
            "<b>Лучшая стратегия</b>",
            f"Рынки: {', '.join(cfg['markets'])}; перевес ≥ {cfg['min_edge']:.0%}; "
            f"кэфы {cfg['min_odds']}–{cfg['max_odds']}",
            "",
            f"Проверка (новые данные): ставок {int(best['test_bets'])}, ROI {best['test_roi']:+.1%}, "
            f"просадка {best['test_max_dd_units']:.1f} ед.",
            f"Весь период: ставок {full['bets']}, ROI {full['roi']:+.1%}, "
            f"угадано {full['hit_rate']:.0%}, банк 1000 → {full['final_bank']:.0f}",
            "",
            "✅ Стратегия прибыльна и на подборе, и на проверке." if cfg["robust"] else
            "⚠️ Надёжной прибыли не найдено — ставки лучше только наблюдать.",
        ]
    Path(out_path).parent.mkdir(parents=True, exist_ok=True)
    Path(out_path).write_text(json.dumps(cfg, indent=2, ensure_ascii=False))
    return "\n".join(lines)
