"""Модель Пуассона (Диксон–Коулз) с затуханием веса старых матчей.

log λ_дом  = μ + home + att[дом] − def[гость]
log λ_гость = μ + att[гость] − def[дом]

Параметры оцениваются взвешенным методом максимального правдоподобия
с L2-регуляризацией (команды с малым числом матчей тянутся к среднему).
Поправка Диксона–Коулза ρ корректирует вероятности счётов 0:0, 1:0, 0:1, 1:1.
"""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pandas as pd
from scipy.optimize import minimize
from scipy.stats import poisson


def _dc_tau(x, y, lh, la, rho):
    tau = np.ones_like(lh, dtype=float)
    m00 = (x == 0) & (y == 0)
    m01 = (x == 0) & (y == 1)
    m10 = (x == 1) & (y == 0)
    m11 = (x == 1) & (y == 1)
    tau[m00] = 1 - lh[m00] * la[m00] * rho
    tau[m01] = 1 + lh[m01] * rho
    tau[m10] = 1 + la[m10] * rho
    tau[m11] = 1 - rho
    return tau


@dataclass
class PoissonModel:
    half_life_days: float = 240.0
    ridge: float = 2.0
    max_goals: int = 10
    fit_rho: bool = True

    teams: list[str] = field(default_factory=list, init=False)
    games: dict[str, float] = field(default_factory=dict, init=False)
    mu: float = field(default=0.0, init=False)
    home_adv: float = field(default=0.0, init=False)
    att: np.ndarray | None = field(default=None, init=False)
    dfn: np.ndarray | None = field(default=None, init=False)
    rho: float = field(default=0.0, init=False)

    def fit(self, matches: pd.DataFrame, ref_date=None) -> "PoissonModel":
        m = matches.dropna(subset=["hg", "ag"])
        if m.empty:
            raise ValueError("Нет сыгранных матчей для обучения")
        ref_date = pd.Timestamp(ref_date) if ref_date is not None else m["date"].max()
        days = (ref_date - m["date"]).dt.days.clip(lower=0).to_numpy(float)
        w = 0.5 ** (days / self.half_life_days)

        self.teams = sorted(set(m["home"]) | set(m["away"]))
        idx = {t: i for i, t in enumerate(self.teams)}
        hi = m["home"].map(idx).to_numpy()
        ai = m["away"].map(idx).to_numpy()
        yh = m["hg"].to_numpy(float)
        ya = m["ag"].to_numpy(float)
        n = len(self.teams)
        self.games = dict(zip(self.teams, np.bincount(hi, minlength=n) + np.bincount(ai, minlength=n)))

        def objective(p):
            mu, home = p[0], p[1]
            att, dfn = p[2:2 + n], p[2 + n:]
            eh = mu + home + att[hi] - dfn[ai]
            ea = mu + att[ai] - dfn[hi]
            lh, la = np.exp(eh), np.exp(ea)
            nll = np.sum(w * (lh - yh * eh)) + np.sum(w * (la - ya * ea))
            nll += 0.5 * self.ridge * (att @ att + dfn @ dfn)
            rh, ra = w * (lh - yh), w * (la - ya)
            g = np.empty_like(p)
            g[0] = rh.sum() + ra.sum()
            g[1] = rh.sum()
            g[2:2 + n] = np.bincount(hi, rh, n) + np.bincount(ai, ra, n) + self.ridge * att
            g[2 + n:] = -np.bincount(ai, rh, n) - np.bincount(hi, ra, n) + self.ridge * dfn
            return nll, g

        x0 = np.zeros(2 + 2 * n)
        x0[0] = np.log(max(np.average((yh + ya) / 2, weights=w), 0.1))
        x0[1] = 0.2
        res = minimize(objective, x0, jac=True, method="L-BFGS-B")
        p = res.x
        self.mu, self.home_adv = float(p[0]), float(p[1])
        self.att, self.dfn = p[2:2 + n], p[2 + n:]

        if self.fit_rho:
            lh = np.exp(self.mu + self.home_adv + self.att[hi] - self.dfn[ai])
            la = np.exp(self.mu + self.att[ai] - self.dfn[hi])
            best, best_ll = 0.0, -np.inf
            for rho in np.linspace(-0.2, 0.2, 81):
                tau = _dc_tau(yh, ya, lh, la, rho)
                if np.any(tau <= 0):
                    continue
                ll = np.sum(w * np.log(tau))
                if ll > best_ll:
                    best, best_ll = rho, ll
            self.rho = float(best)
        return self

    def knows(self, team: str) -> bool:
        return team in self.games

    def expected_goals(self, home: str, away: str) -> tuple[float, float]:
        idx = {t: i for i, t in enumerate(self.teams)}
        # неизвестная команда (например, новичок лиги) — средняя сила с поправкой вниз
        a_h = self.att[idx[home]] if home in idx else -0.15
        d_h = self.dfn[idx[home]] if home in idx else -0.15
        a_a = self.att[idx[away]] if away in idx else -0.15
        d_a = self.dfn[idx[away]] if away in idx else -0.15
        lh = np.exp(self.mu + self.home_adv + a_h - d_a)
        la = np.exp(self.mu + a_a - d_h)
        return float(lh), float(la)

    def score_matrix(self, lh: float, la: float) -> np.ndarray:
        g = np.arange(self.max_goals + 1)
        mat = np.outer(poisson.pmf(g, lh), poisson.pmf(g, la))
        mat[0, 0] *= 1 - lh * la * self.rho
        mat[0, 1] *= 1 + lh * self.rho
        mat[1, 0] *= 1 + la * self.rho
        mat[1, 1] *= 1 - self.rho
        return mat / mat.sum()

    def predict(self, home: str, away: str) -> dict:
        lh, la = self.expected_goals(home, away)
        mat = self.score_matrix(lh, la)
        g = np.arange(mat.shape[0])
        total = g[:, None] + g[None, :]
        return {
            "xg_home": lh,
            "xg_away": la,
            "H": float(np.tril(mat, -1).sum()),
            "D": float(np.trace(mat)),
            "A": float(np.triu(mat, 1).sum()),
            "O25": float(mat[total >= 3].sum()),
            "U25": float(mat[total <= 2].sum()),
        }

    def ratings(self) -> pd.DataFrame:
        df = pd.DataFrame({"team": self.teams, "attack": self.att, "defence": self.dfn,
                           "games": [self.games[t] for t in self.teams]})
        df["strength"] = df["attack"] + df["defence"]
        return df.sort_values("strength", ascending=False).reset_index(drop=True)
