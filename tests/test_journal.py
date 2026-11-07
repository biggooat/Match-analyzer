import pandas as pd

from match_analyzer import journal as jr


def _bets(date, time="15:00", sel="H", price=2.0):
    return pd.DataFrame([{"league": "E0", "date": pd.Timestamp(date), "time": time, "home": "A", "away": "B",
                          "selection": sel, "price": price, "p": 0.55, "edge": 0.1, "stake_frac": 0.02}])


def test_add_only_future_and_once_per_match(tmp_path):
    now = pd.Timestamp("2026-11-07 10:00", tz="Europe/London")
    j = jr.load(tmp_path / "j.csv")
    j = jr.add_bets(j, _bets("2026-11-07", "09:00"), now=now)   # уже начался
    assert j.empty
    j = jr.add_bets(j, _bets("2026-11-08"), now=now)
    j = jr.add_bets(j, _bets("2026-11-08", sel="O25"), now=now)  # тот же матч — не дублируем
    assert len(j) == 1 and j.iloc[0]["status"] == "open"


def test_settle_and_summary(tmp_path):
    now = pd.Timestamp("2026-11-07 10:00", tz="Europe/London")
    j = jr.add_bets(jr.load(tmp_path / "j.csv"), _bets("2026-11-08", price=2.5), now=now)
    res = pd.DataFrame([{"league": "E0", "date": pd.Timestamp("2026-11-08"), "home": "A", "away": "B", "hg": 2.0, "ag": 1.0}])
    j = jr.settle(j, res, today=pd.Timestamp("2026-11-09"))
    r = j.iloc[0]
    assert r["status"] == "won" and r["score"] == "2:1" and abs(r["units"] - 1.5) < 1e-9
    path = tmp_path / "j.csv"
    jr.save(j, path)
    j2 = jr.load(path)
    s = jr.summary(j2)
    assert s["settled"] == 1 and s["won"] == 1 and abs(s["roi"] - 1.5) < 1e-9 and abs(s["bank_growth"] - 0.03) < 1e-9


def test_postponed_match_becomes_void(tmp_path):
    now = pd.Timestamp("2026-10-01 10:00", tz="Europe/London")
    j = jr.add_bets(jr.load(tmp_path / "j.csv"), _bets("2026-10-02"), now=now)
    j = jr.settle(j, pd.DataFrame(columns=["league", "date", "home", "away", "hg", "ag"]), today=pd.Timestamp("2026-11-01"))
    assert j.iloc[0]["status"] == "void" and jr.summary(j)["settled"] == 0
