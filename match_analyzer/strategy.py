"""Превращение прогнозов в ставки: поиск value, фильтры риска и размер ставки."""
from __future__ import annotations

from dataclasses import asdict, dataclass

import numpy as np
import pandas as pd

from .data import SELECTIONS
from .model import PoissonModel

MARKET_OF = {"H": "1X2", "D": "1X2", "A": "1X2", "O25": "OU25", "U25": "OU25"}
LABEL = {"H": "П1", "D": "Х", "A": "П2", "O25": "ТБ 2.5", "U25": "ТМ 2.5"}


def devig(odds: np.ndarray) -> np.ndarray:
    """Убирает маржу букмекера: вероятности пропорциональны 1/коэффициент."""
    inv = 1.0 / odds
    return inv / inv.sum(axis=1, keepdims=True)


def _pick_odds(df: pd.DataFrame, sources: list[str], sel: str) -> pd.Series:
    out = pd.Series(np.nan, index=df.index)
    for src in sources:
        col = f"odds_{src}_{sel}"
        if col in df.columns:
            out = out.fillna(df[col])
    return out


def build_selections(matches: pd.DataFrame, probs: pd.DataFrame,
                     price_sources=("max", "avg", "b365"),
                     ref_sources=("pinnacle", "avg", "b365")) -> pd.DataFrame:
    """Длинная таблица: одна строка = один исход матча.

    price — коэффициент, по которому ставим (по умолчанию лучший на рынке);
    market_p — «честная» вероятность от букмекеров без маржи (Pinnacle — самая точная линия).
    """
    df = matches.reset_index(drop=True).join(probs.reset_index(drop=True))
    price = {s: _pick_odds(df, list(price_sources), s) for s in SELECTIONS}
    ref = {s: _pick_odds(df, list(ref_sources), s) for s in SELECTIONS}

    market_p = {}
    for group in (("H", "D", "A"), ("O25", "U25")):
        arr = np.column_stack([ref[s].to_numpy(float) for s in group])
        p = devig(arr)
        for i, s in enumerate(group):
            market_p[s] = p[:, i]

    played = df["hg"].notna() & df["ag"].notna()
    hg, ag = df["hg"], df["ag"]
    won = {"H": hg > ag, "D": hg == ag, "A": hg < ag,
           "O25": hg + ag >= 3, "U25": hg + ag <= 2}

    rows = []
    base_cols = [c for c in ("date", "time", "league", "season", "home", "away", "hg", "ag",
                             "xg_home", "xg_away", "min_games") if c in df.columns]
    for s in SELECTIONS:
        part = df[base_cols].copy()
        part["selection"] = s
        part["market"] = MARKET_OF[s]
        part["model_p"] = df[s].to_numpy(float)
        part["market_p"] = market_p[s]
        part["price"] = price[s].to_numpy(float)
        part["won"] = np.where(played, won[s], np.nan)
        rows.append(part)
    out = pd.concat(rows, ignore_index=True)
    return out.dropna(subset=["price", "model_p"]).sort_values(["date", "home", "selection"]).reset_index(drop=True)


@dataclass
class Strategy:
    """Правила отбора ставок.

    Прибыльность даёт edge (перевес над линией), а «непрогораемость» —
    фильтры по коэффициентам/вероятности и дробный Келли с лимитом на ставку.
    """
    markets: tuple = ("1X2", "OU25")
    min_edge: float = 0.05        # мин. матожидание ставки: p*k − 1
    min_odds: float = 1.5
    max_odds: float = 3.5
    min_prob: float = 0.30        # не ставим на маловероятные исходы
    market_weight: float = 0.5    # доля рыночной вероятности в итоговой оценке (0 = только модель)
    max_disagree: float = 0.12    # если модель и рынок расходятся сильнее — скорее ошибка модели
    min_games: int = 8            # обе команды должны иметь достаточно истории
    staking: str = "kelly"        # "kelly" или "flat"
    kelly_fraction: float = 0.25
    max_stake: float = 0.03       # не больше 3% банка на одну ставку
    flat_stake: float = 0.01
    one_per_match: bool = True

    def to_dict(self) -> dict:
        return asdict(self)

    def select(self, selections: pd.DataFrame) -> pd.DataFrame:
        df = selections.copy()
        mp = df["market_p"].fillna(df["model_p"])
        df["p"] = (1 - self.market_weight) * df["model_p"] + self.market_weight * mp
        df["edge"] = df["p"] * df["price"] - 1
        mask = (
            df["market"].isin(self.markets)
            & (df["edge"] >= self.min_edge)
            & df["price"].between(self.min_odds, self.max_odds)
            & (df["p"] >= self.min_prob)
            & ((df["model_p"] - mp).abs() <= self.max_disagree)
        )
        if "min_games" in df.columns:
            mask &= df["min_games"] >= self.min_games
        bets = df[mask]
        if self.one_per_match and not bets.empty:
            bets = (bets.sort_values("edge", ascending=False)
                        .drop_duplicates(["date", "home", "away"])
                        .sort_values(["date", "home"]))
        bets = bets.copy()
        if self.staking == "flat":
            bets["stake_frac"] = self.flat_stake
        else:
            kelly = (bets["p"] * bets["price"] - 1) / (bets["price"] - 1)
            bets["stake_frac"] = (self.kelly_fraction * kelly).clip(lower=0, upper=self.max_stake)
        return bets.reset_index(drop=True)


def predict_matches(model: PoissonModel, matches: pd.DataFrame) -> pd.DataFrame:
    rows = []
    for h, a in zip(matches["home"], matches["away"]):
        r = model.predict(h, a)
        r["min_games"] = min(model.games.get(h, 0), model.games.get(a, 0))
        rows.append(r)
    return pd.DataFrame(rows, index=matches.index)
