"""Честный бэктест «по ходу времени» (walk-forward) и подбор стратегии.

Модель каждый раз обучается только на матчах, сыгранных ДО прогнозируемого
тура, так что в результатах нет заглядывания в будущее.
"""
from __future__ import annotations

import itertools
from dataclasses import replace

import numpy as np
import pandas as pd

from .model import PoissonModel
from .strategy import Strategy, build_selections, predict_matches


def walk_forward(matches: pd.DataFrame, model_kwargs: dict | None = None,
                 refit_days: int = 7, min_train: int = 150, lookback_days: int = 3 * 365,
                 test_from=None, verbose: bool = True, **sel_kwargs) -> pd.DataFrame:
    model_kwargs = model_kwargs or {}
    played = matches.dropna(subset=["hg", "ag"]).sort_values("date").reset_index(drop=True)
    out = []
    for league, lm in played.groupby("league"):
        lm = lm.reset_index(drop=True)
        start = lm["date"].iloc[min(min_train, len(lm) - 1)]
        if test_from is not None:
            start = max(start, pd.Timestamp(test_from))
        periods = pd.date_range(start.normalize(), lm["date"].max() + pd.Timedelta(days=1),
                                freq=f"{refit_days}D")
        n_done = 0
        for lo, hi in zip(periods[:-1], periods[1:]):
            test = lm[(lm["date"] >= lo) & (lm["date"] < hi)]
            if test.empty:
                continue
            train = lm[(lm["date"] < lo) & (lm["date"] >= lo - pd.Timedelta(days=lookback_days))]
            if len(train) < min_train:
                continue
            model = PoissonModel(**model_kwargs).fit(train, ref_date=lo)
            out.append(build_selections(test, predict_matches(model, test), **sel_kwargs))
            n_done += len(test)
        if verbose:
            print(f"  {league}: спрогнозировано {n_done} матчей")
    if not out:
        raise RuntimeError("Недостаточно данных для бэктеста")
    return pd.concat(out, ignore_index=True)


def simulate(selections: pd.DataFrame, strategy: Strategy, bank: float = 1000.0) -> pd.DataFrame:
    """Проигрывает стратегию по истории. Ставки одного дня считаются от банка на начало дня."""
    bets = strategy.select(selections)
    bets = bets.dropna(subset=["won"]).sort_values("date").reset_index(drop=True)
    if bets.empty:
        return bets
    won = bets["won"].astype(bool).to_numpy()
    price = bets["price"].to_numpy()
    frac = bets["stake_frac"].to_numpy()
    days = bets["date"].to_numpy()
    stakes = np.empty(len(bets))
    pnl = np.empty(len(bets))
    banks = np.empty(len(bets))
    cur = bank
    i = 0
    while i < len(bets):
        j = i
        while j < len(bets) and days[j] == days[i]:
            j += 1
        day_bank = cur
        for k in range(i, j):
            stakes[k] = day_bank * frac[k]
            pnl[k] = stakes[k] * (price[k] - 1) if won[k] else -stakes[k]
            cur += pnl[k]
            banks[k] = cur
        i = j
    bets["stake"] = stakes
    bets["pnl"] = pnl
    bets["bank"] = banks
    bets["units"] = np.where(won, price - 1, -1.0)  # результат при ставке 1 единица
    return bets


def _max_drawdown(curve: np.ndarray) -> float:
    peak = np.maximum.accumulate(np.concatenate([[0.0], curve]))
    return float((peak[1:] - curve).max()) if len(curve) else 0.0


def _longest_losing(won: np.ndarray) -> int:
    best = cur = 0
    for w in won:
        cur = 0 if w else cur + 1
        best = max(best, cur)
    return best


def summarize(bets: pd.DataFrame, bank: float = 1000.0) -> dict:
    if bets is None or bets.empty:
        return {"bets": 0}
    units = bets["units"].to_numpy()
    won = bets["won"].astype(bool).to_numpy()
    curve = np.cumsum(units)
    n = len(units)
    std = units.std(ddof=1) if n > 1 else np.nan
    bank_curve = bets["bank"].to_numpy()
    bank_peak = np.maximum.accumulate(np.concatenate([[bank], bank_curve]))[1:]
    return {
        "bets": n,
        "hit_rate": won.mean(),
        "avg_odds": bets["price"].mean(),
        "profit_units": curve[-1],
        "roi": units.mean(),
        "max_dd_units": _max_drawdown(curve),
        "losing_streak": _longest_losing(won),
        "t_stat": units.mean() / std * np.sqrt(n) if n > 1 and std > 0 else np.nan,
        "final_bank": bank_curve[-1],
        "bank_growth": bank_curve[-1] / bank - 1,
        "max_dd_bank": float(((bank_peak - bank_curve) / bank_peak).max()),
    }


def breakdown(bets: pd.DataFrame, by: str) -> pd.DataFrame:
    if bets.empty:
        return pd.DataFrame()
    g = bets.groupby(by)
    return pd.DataFrame({
        "bets": g.size(),
        "hit_rate": g["won"].mean(),
        "avg_odds": g["price"].mean(),
        "profit_units": g["units"].sum(),
        "roi": g["units"].mean(),
    })


DEFAULT_GRID = {
    "markets": [("1X2",), ("OU25",), ("1X2", "OU25")],
    "min_edge": [0.02, 0.04, 0.06, 0.08, 0.12],
    "odds_range": [(1.3, 2.2), (1.5, 3.0), (1.8, 4.0), (1.3, 5.0)],
    "market_weight": [0.0, 0.3, 0.5, 0.7],
    "min_prob": [0.0, 0.35, 0.5],
}


def optimize(selections: pd.DataFrame, grid: dict | None = None, base: Strategy | None = None,
             min_bets: int = 100, holdout_from=None) -> pd.DataFrame:
    """Перебирает настройки стратегии.

    Ранжирует по t-статистике прибыли (прибыль относительно разброса результатов) —
    это одновременно «самые прибыльные» и «менее прогорающие» настройки.
    Если задан holdout_from, подбор идёт на данных ДО этой даты, а итог
    проверяется на данных ПОСЛЕ — это защита от подгонки под историю.
    """
    grid = grid or DEFAULT_GRID
    base = base or Strategy()
    if holdout_from is not None:
        cut = pd.Timestamp(holdout_from)
        train, test = selections[selections["date"] < cut], selections[selections["date"] >= cut]
    else:
        train, test = selections, None

    keys = list(grid)
    rows = []
    for combo in itertools.product(*(grid[k] for k in keys)):
        params = dict(zip(keys, combo))
        kw = {k: v for k, v in params.items() if k != "odds_range"}
        if "odds_range" in params:
            kw["min_odds"], kw["max_odds"] = params["odds_range"]
        strat = replace(base, **kw)
        s = summarize(simulate(train, strat))
        if s["bets"] < min_bets:
            continue
        row = {**params, **{f"train_{k}": v for k, v in s.items()}}
        if test is not None:
            st = summarize(simulate(test, strat))
            row.update({f"test_{k}": v for k, v in st.items()})
        rows.append(row)
    res = pd.DataFrame(rows)
    if res.empty:
        return res
    return res.sort_values("train_t_stat", ascending=False).reset_index(drop=True)
