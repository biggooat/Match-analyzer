import json

from match_analyzer import telegram
from match_analyzer.service import load_strategy, tips_message, upcoming_bets, weekly_report
from match_analyzer.strategy import Strategy
from match_analyzer.synthetic import make_league


def _demo_bets():
    m = make_league(n_seasons=2, seed=5)
    last = m["date"].max()
    return upcoming_bets(m[m["date"] < last], m[m["date"] == last], Strategy(min_edge=0.0, min_prob=0))


def test_tips_message_html():
    bets, n = _demo_bets()
    assert n == 10 and not bets.empty
    text = tips_message(bets, n, {"robust": False})
    assert "Ставки на ближайшие матчи" in text and "⚠️" in text and "@" in text


def test_tips_message_no_bets():
    import pandas as pd
    assert "Подходящих ставок нет" in tips_message(pd.DataFrame(), 5)


def test_weekly_report_writes_strategy(tmp_path):
    out = tmp_path / "best.json"
    text = weekly_report(["XX"], 3, out, matches=make_league(n_seasons=3, seed=6))
    cfg = json.loads(out.read_text())
    assert "robust" in cfg and "Еженедельная проверка" in text
    strat, cfg2 = load_strategy(out)
    assert isinstance(strat, Strategy) and cfg2 == cfg


def test_split_and_send(monkeypatch):
    calls = []
    monkeypatch.setattr(telegram, "api", lambda token, method, **p: calls.append((method, p)))
    telegram.send("T", 1, "\n".join(["x" * 100] * 100))
    assert len(calls) == 3 and all(len(p["text"]) <= telegram.LIMIT for _, p in calls)
    assert calls[0][1]["parse_mode"] == "HTML"
