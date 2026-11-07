"""Журнал рекомендованных ставок: запись до матча и расчёт результата после."""
from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd

COLUMNS = ["key", "league", "date", "time", "home", "away", "selection", "price", "p", "edge", "stake",
           "added", "status", "score", "units", "bank_pct"]
VOID_AFTER_DAYS = 14  # матч не нашёлся в результатах за это время — считаем перенесённым/отменённым


def load(path: str | Path) -> pd.DataFrame:
    path = Path(path)
    if not path.exists():
        return pd.DataFrame(columns=COLUMNS)
    df = pd.read_csv(path, dtype={"time": str, "score": str, "key": str})
    for c in COLUMNS:
        if c not in df.columns:
            df[c] = np.nan
    df["date"] = pd.to_datetime(df["date"])
    df["time"] = df["time"].fillna("")
    df["score"] = df["score"].fillna("")
    return df[COLUMNS]


def save(journal: pd.DataFrame, path: str | Path) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    out = journal.copy()
    out["date"] = pd.to_datetime(out["date"]).dt.strftime("%Y-%m-%d")
    out.sort_values(["date", "time", "league", "home"]).to_csv(path, index=False)


def _match_key(league, date, home, away) -> str:
    return f"{league}|{pd.Timestamp(date):%Y-%m-%d}|{home}|{away}"


def _kickoff_london(date, time) -> pd.Timestamp:
    try:
        hh, mm = (int(x) for x in str(time).split(":")[:2])
    except ValueError:
        hh, mm = 23, 59
    d = pd.Timestamp(date)
    return pd.Timestamp(d.year, d.month, d.day, hh, mm, tz="Europe/London")


def add_bets(journal: pd.DataFrame, bets: pd.DataFrame, now: pd.Timestamp | None = None) -> pd.DataFrame:
    """Добавляет новые рекомендации к ещё не начавшимся матчам (один исход на матч — первый рекомендованный)."""
    if bets is None or bets.empty:
        return journal
    now = now or pd.Timestamp.now(tz="Europe/London")
    known = {k.rsplit("|", 1)[0] for k in journal["key"].dropna()}
    rows = []
    for _, b in bets.iterrows():
        time = str(b.get("time", "") or "")
        if _kickoff_london(b["date"], time) <= now:
            continue
        mk = _match_key(b["league"], b["date"], b["home"], b["away"])
        if mk in known:
            continue
        known.add(mk)
        rows.append({
            "key": f"{mk}|{b['selection']}", "league": b["league"], "date": pd.Timestamp(b["date"]),
            "time": time, "home": b["home"], "away": b["away"], "selection": b["selection"],
            "price": round(float(b["price"]), 2), "p": round(float(b["p"]), 4),
            "edge": round(float(b["edge"]), 4), "stake": round(float(b["stake_frac"]), 4),
            "added": now.tz_convert("Europe/Moscow").strftime("%Y-%m-%d %H:%M"),
            "status": "open", "score": "", "units": np.nan, "bank_pct": np.nan,
        })
    if not rows:
        return journal
    new = pd.DataFrame(rows, columns=COLUMNS)
    return new if journal.empty else pd.concat([journal, new], ignore_index=True)


def _won(selection: str, hg: float, ag: float) -> bool:
    return {"H": hg > ag, "D": hg == ag, "A": hg < ag, "O25": hg + ag >= 3, "U25": hg + ag <= 2}[selection]


def settle(journal: pd.DataFrame, results: pd.DataFrame, today: pd.Timestamp | None = None) -> pd.DataFrame:
    """Проставляет результат открытым ставкам по сыгранным матчам."""
    if journal.empty:
        return journal
    today = pd.Timestamp(today or pd.Timestamp.now(tz="Europe/London").date())
    j = journal.copy()
    played = results.dropna(subset=["hg", "ag"])
    by_pair = {k: g for k, g in played.groupby(["league", "home", "away"])}
    for i in j.index[j["status"] == "open"]:
        r = j.loc[i]
        g = by_pair.get((r["league"], r["home"], r["away"]))
        hit = None
        if g is not None:
            near = g[(g["date"] - r["date"]).abs() <= pd.Timedelta(days=3)]
            if not near.empty:
                hit = near.iloc[0]
        if hit is not None:
            hg, ag = float(hit["hg"]), float(hit["ag"])
            won = _won(r["selection"], hg, ag)
            j.at[i, "status"] = "won" if won else "lost"
            j.at[i, "score"] = f"{int(hg)}:{int(ag)}"
            j.at[i, "units"] = round(r["price"] - 1, 4) if won else -1.0
            j.at[i, "bank_pct"] = round(r["stake"] * (r["price"] - 1), 6) if won else -r["stake"]
        elif r["date"] < today - pd.Timedelta(days=VOID_AFTER_DAYS):
            j.at[i, "status"] = "void"
            j.at[i, "units"] = 0.0
            j.at[i, "bank_pct"] = 0.0
    return j


def summary(journal: pd.DataFrame) -> dict:
    j = journal
    done = j[j["status"].isin(["won", "lost"])]
    n = len(done)
    won = int((done["status"] == "won").sum())
    units = float(done["units"].sum()) if n else 0.0
    # рост банка, если ставить рекомендованный % от текущего банка
    bank = float(np.prod(1 + done.sort_values("date")["bank_pct"].to_numpy())) if n else 1.0
    return {
        "total": int(len(j)), "settled": n, "won": won, "lost": n - won,
        "open": int((j["status"] == "open").sum()), "void": int((j["status"] == "void").sum()),
        "hit_rate": won / n if n else None, "profit_units": round(units, 2),
        "roi": units / n if n else None, "bank_growth": round(bank - 1, 4),
    }
