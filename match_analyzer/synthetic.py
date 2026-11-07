"""Синтетическая лига для демо и тестов (работает без интернета)."""
from __future__ import annotations

import numpy as np
import pandas as pd


def make_league(n_teams: int = 20, n_seasons: int = 4, seed: int = 0,
                margin: float = 0.05, noise: float = 0.25, league: str = "XX") -> pd.DataFrame:
    """Генерирует сезоны с «истинными» силами команд и неточной линией букмекеров.

    noise — насколько линия букмекера ошибается (в логарифме ожидаемых голов).
    """
    rng = np.random.default_rng(seed)
    teams = [f"Team{i:02d}" for i in range(n_teams)]
    att = rng.normal(0, 0.3, n_teams)
    dfn = rng.normal(0, 0.3, n_teams)
    mu, home = 0.1, 0.25
    rows = []
    for season in range(n_seasons):
        att = 0.8 * att + rng.normal(0, 0.12, n_teams)  # силы меняются между сезонами
        dfn = 0.8 * dfn + rng.normal(0, 0.12, n_teams)
        start = pd.Timestamp(2020 + season, 8, 10)
        pairs = [(h, a) for h in range(n_teams) for a in range(n_teams) if h != a]
        rng.shuffle(pairs)
        per_round = n_teams // 2
        for k, (h, a) in enumerate(pairs):
            date = start + pd.Timedelta(days=7 * (k // per_round))
            lh = np.exp(mu + home + att[h] - dfn[a])
            la = np.exp(mu + att[a] - dfn[h])
            hg, ag = rng.poisson(lh), rng.poisson(la)
            # букмекер видит силы с ошибкой
            blh = lh * np.exp(rng.normal(0, noise))
            bla = la * np.exp(rng.normal(0, noise))
            g = np.arange(11)
            from scipy.stats import poisson
            mat = np.outer(poisson.pmf(g, blh), poisson.pmf(g, bla))
            mat /= mat.sum()
            tot = g[:, None] + g[None, :]
            p = {"H": np.tril(mat, -1).sum(), "D": np.trace(mat), "A": np.triu(mat, 1).sum(),
                 "O25": mat[tot >= 3].sum(), "U25": mat[tot <= 2].sum()}
            row = {"date": date, "league": league, "season": str(season), "home": teams[h],
                   "away": teams[a], "hg": float(hg), "ag": float(ag)}
            for src, m in (("avg", margin), ("max", margin * 0.4), ("pinnacle", margin * 0.5)):
                for s, prob in p.items():
                    row[f"odds_{src}_{s}"] = round(1 / (prob * (1 + m)), 2)
            rows.append(row)
    return pd.DataFrame(rows).sort_values("date").reset_index(drop=True)
