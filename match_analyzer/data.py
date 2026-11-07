"""Загрузка и нормализация данных football-data.co.uk.

Каждый CSV сезона содержит результаты матчей и коэффициенты букмекеров
(Bet365, Pinnacle, среднее и максимальное по рынку) на 1X2 и тотал 2.5.
"""
from __future__ import annotations

import datetime as dt
import io
import os
import urllib.request
from pathlib import Path
from typing import Iterable

import numpy as np
import pandas as pd

BASE_URL = "https://www.football-data.co.uk"
FIXTURES_URL = f"{BASE_URL}/fixtures.csv"

LEAGUES = {
    "E0": "Англия — Премьер-лига",
    "E1": "Англия — Чемпионшип",
    "E2": "Англия — Лига 1",
    "SC0": "Шотландия — Премьершип",
    "D1": "Германия — Бундеслига",
    "D2": "Германия — 2. Бундеслига",
    "SP1": "Испания — Ла Лига",
    "SP2": "Испания — Сегунда",
    "I1": "Италия — Серия A",
    "I2": "Италия — Серия B",
    "F1": "Франция — Лига 1",
    "F2": "Франция — Лига 2",
    "N1": "Нидерланды — Эредивизи",
    "B1": "Бельгия — Про Лига",
    "P1": "Португалия — Примейра",
    "T1": "Турция — Суперлига",
    "G1": "Греция — Суперлига",
}

SELECTIONS = ("H", "D", "A", "O25", "U25")

# Источник цены -> {исход: [возможные названия колонок]}
_ODDS_COLUMNS = {
    "max": {"H": ["MaxH", "BbMxH"], "D": ["MaxD", "BbMxD"], "A": ["MaxA", "BbMxA"],
            "O25": ["Max>2.5", "BbMx>2.5"], "U25": ["Max<2.5", "BbMx<2.5"]},
    "avg": {"H": ["AvgH", "BbAvH"], "D": ["AvgD", "BbAvD"], "A": ["AvgA", "BbAvA"],
            "O25": ["Avg>2.5", "BbAv>2.5"], "U25": ["Avg<2.5", "BbAv<2.5"]},
    "pinnacle": {"H": ["PSH"], "D": ["PSD"], "A": ["PSA"],
                 "O25": ["P>2.5"], "U25": ["P<2.5"]},
    "b365": {"H": ["B365H"], "D": ["B365D"], "A": ["B365A"],
             "O25": ["B365>2.5"], "U25": ["B365<2.5"]},
    "pinnacle_close": {"H": ["PSCH"], "D": ["PSCD"], "A": ["PSCA"],
                       "O25": ["PC>2.5"], "U25": ["PC<2.5"]},
    "avg_close": {"H": ["AvgCH"], "D": ["AvgCD"], "A": ["AvgCA"],
                  "O25": ["AvgC>2.5"], "U25": ["AvgC<2.5"]},
    "max_close": {"H": ["MaxCH"], "D": ["MaxCD"], "A": ["MaxCA"],
                  "O25": ["MaxC>2.5"], "U25": ["MaxC<2.5"]},
    "b365_close": {"H": ["B365CH"], "D": ["B365CD"], "A": ["B365CA"],
                   "O25": ["B365C>2.5"], "U25": ["B365C<2.5"]},
}
PRICE_SOURCES = tuple(_ODDS_COLUMNS)


def cache_dir() -> Path:
    path = Path(os.environ.get("MATCH_ANALYZER_CACHE", "~/.cache/match-analyzer")).expanduser()
    path.mkdir(parents=True, exist_ok=True)
    return path


def current_season(today: dt.date | None = None) -> str:
    """Код сезона football-data, например '2526' для сезона 2025/26."""
    today = today or dt.date.today()
    start = today.year if today.month >= 7 else today.year - 1
    return f"{start % 100:02d}{(start + 1) % 100:02d}"


def previous_seasons(n: int, today: dt.date | None = None) -> list[str]:
    """Последние n сезонов, включая текущий, от старых к новым."""
    cur = int(current_season(today)[:2])
    return [f"{(cur - i) % 100:02d}{(cur - i + 1) % 100:02d}" for i in range(n - 1, -1, -1)]


def season_url(league: str, season: str) -> str:
    return f"{BASE_URL}/mmz4281/{season}/{league}.csv"


def _download(url: str, dest: Path, force: bool = False) -> Path:
    if dest.exists() and not force:
        return dest
    req = urllib.request.Request(url, headers={"User-Agent": "match-analyzer/0.1"})
    with urllib.request.urlopen(req, timeout=60) as resp:
        payload = resp.read()
    dest.write_bytes(payload)
    return dest


def _read_csv(source) -> pd.DataFrame:
    if isinstance(source, (str, Path)):
        raw = Path(source).read_bytes()
    else:
        raw = source
    text = raw.decode("utf-8-sig", errors="replace")
    df = pd.read_csv(io.StringIO(text), on_bad_lines="skip")
    df = df.loc[:, ~df.columns.astype(str).str.startswith("Unnamed")]
    return df.dropna(how="all")


def _first_column(df: pd.DataFrame, names: Iterable[str]) -> pd.Series:
    for name in names:
        if name in df.columns:
            return pd.to_numeric(df[name], errors="coerce")
    return pd.Series(np.nan, index=df.index)


def normalize(raw: pd.DataFrame, season: str | None = None) -> pd.DataFrame:
    """Приводит CSV football-data к единому формату."""
    out = pd.DataFrame(index=raw.index)
    out["date"] = pd.to_datetime(raw["Date"], dayfirst=True, format="mixed", errors="coerce")
    out["league"] = raw.get("Div", pd.Series("", index=raw.index)).astype(str)
    out["season"] = season or ""
    out["home"] = raw["HomeTeam"].astype(str).str.strip()
    out["away"] = raw["AwayTeam"].astype(str).str.strip()
    out["hg"] = _first_column(raw, ["FTHG", "HG"])
    out["ag"] = _first_column(raw, ["FTAG", "AG"])
    for src, mapping in _ODDS_COLUMNS.items():
        for sel, names in mapping.items():
            col = _first_column(raw, names)
            out[f"odds_{src}_{sel}"] = col.where(col > 1.0)
    out = out.dropna(subset=["date"])
    out = out[(out["home"] != "nan") & (out["away"] != "nan")]
    return out.sort_values("date").reset_index(drop=True)


def load_season(league: str, season: str, force: bool = False) -> pd.DataFrame:
    dest = cache_dir() / f"{league}_{season}.csv"
    # текущий сезон обновляется каждую неделю — перекачиваем, если файл старше суток
    if dest.exists() and season == current_season():
        age = dt.datetime.now().timestamp() - dest.stat().st_mtime
        force = force or age > 24 * 3600
    _download(season_url(league, season), dest, force=force)
    df = normalize(_read_csv(dest), season=season)
    df["league"] = league
    return df


def load_history(leagues: Iterable[str], seasons: Iterable[str], force: bool = False,
                 verbose: bool = True) -> pd.DataFrame:
    frames = []
    for league in leagues:
        for season in seasons:
            try:
                frames.append(load_season(league, season, force=force))
            except Exception as exc:  # сезон может отсутствовать
                if verbose:
                    print(f"[!] {league} {season}: не удалось загрузить ({exc})")
    if not frames:
        raise RuntimeError("Не удалось загрузить ни одного сезона")
    return pd.concat(frames, ignore_index=True).sort_values("date").reset_index(drop=True)


def load_fixtures(leagues: Iterable[str] | None = None) -> pd.DataFrame:
    dest = cache_dir() / "fixtures.csv"
    _download(FIXTURES_URL, dest, force=True)
    df = normalize(_read_csv(dest))
    if leagues:
        df = df[df["league"].isin(list(leagues))]
    return df.reset_index(drop=True)


def load_local(paths: Iterable[str | Path]) -> pd.DataFrame:
    frames = [normalize(_read_csv(p), season=Path(p).stem) for p in paths]
    return pd.concat(frames, ignore_index=True).sort_values("date").reset_index(drop=True)
