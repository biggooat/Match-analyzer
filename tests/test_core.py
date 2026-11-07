import numpy as np
import pandas as pd

from match_analyzer import data
from match_analyzer.backtest import optimize, simulate, summarize, walk_forward
from match_analyzer.model import PoissonModel
from match_analyzer.strategy import Strategy, build_selections, devig, predict_matches
from match_analyzer.synthetic import make_league

RAW = b"""Div,Date,Time,HomeTeam,AwayTeam,FTHG,FTAG,FTR,B365H,B365D,B365A,PSH,PSD,PSA,MaxH,MaxD,MaxA,AvgH,AvgD,AvgA,Avg>2.5,Avg<2.5,Max>2.5,Max<2.5,P>2.5,P<2.5,
E0,16/08/2024,20:00,Man United,Fulham,1,0,H,1.6,4.2,5.25,1.63,4.3,5.5,1.67,4.4,5.6,1.62,4.2,5.2,1.8,2.0,1.85,2.05,1.82,2.03,
E0,17/08/2024,12:30,Ipswich,Liverpool,0,2,A,8,5.25,1.36,8.2,5.4,1.38,8.6,5.6,1.4,7.9,5.2,1.37,1.5,2.6,1.55,2.7,1.52,2.6,
"""


def test_normalize_football_data_csv():
    df = data.normalize(data._read_csv(RAW), season="2425")
    assert list(df["home"]) == ["Man United", "Ipswich"]
    assert df["date"].iloc[0] == pd.Timestamp(2024, 8, 16)
    assert df["odds_max_H"].iloc[0] == 1.67
    assert df["odds_pinnacle_O25"].iloc[1] == 1.52
    assert df["odds_pinnacle_close_H"].isna().all()


def test_season_codes():
    import datetime as dt
    assert data.current_season(dt.date(2026, 11, 7)) == "2627"
    assert data.current_season(dt.date(2026, 3, 1)) == "2526"
    assert data.previous_seasons(3, dt.date(2026, 11, 7)) == ["2425", "2526", "2627"]


def test_model_probabilities_are_consistent():
    m = make_league(n_seasons=2, seed=1)
    model = PoissonModel().fit(m)
    p = model.predict("Team00", "Team01")
    assert abs(p["H"] + p["D"] + p["A"] - 1) < 1e-9
    assert abs(p["O25"] + p["U25"] - 1) < 1e-9
    # домашнее преимущество должно быть найдено
    assert model.home_adv > 0


def test_devig():
    p = devig(np.array([[2.0, 3.5, 4.0]]))
    assert abs(p.sum() - 1) < 1e-12 and p[0, 0] > p[0, 2]


def test_strategy_filters_and_kelly_cap():
    m = make_league(n_seasons=2, seed=2)
    model = PoissonModel().fit(m)
    last = m[m["date"] == m["date"].max()]
    sel = build_selections(last, predict_matches(model, last))
    s = Strategy(min_edge=-1, min_odds=1.0, max_odds=100, min_prob=0, max_disagree=1, min_games=0,
                 max_stake=0.02, one_per_match=True)
    bets = s.select(sel)
    assert len(bets) == len(last)
    assert (bets["stake_frac"] <= 0.02).all()


def test_walk_forward_has_no_lookahead_and_simulates():
    m = make_league(n_seasons=2, seed=3)
    sel = walk_forward(m, min_train=200, verbose=False)
    assert sel["date"].min() > m["date"].iloc[199]
    bets = simulate(sel, Strategy(min_edge=0.0))
    s = summarize(bets)
    assert s["bets"] > 0 and np.isfinite(s["final_bank"])
    res = optimize(sel, grid={"min_edge": [0.0, 0.05]}, min_bets=1, holdout_from=sel["date"].median())
    assert "test_roi" in res.columns
