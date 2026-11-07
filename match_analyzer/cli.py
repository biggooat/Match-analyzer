"""Командная строка: python -m match_analyzer <команда> ..."""
from __future__ import annotations

import argparse
import json
import os
import sys
from dataclasses import replace
from pathlib import Path

import pandas as pd

from . import data
from .backtest import breakdown, optimize, simulate, summarize, walk_forward
from .service import fetch_upcoming, load_strategy, tips_message, upcoming_bets, weekly_report
from .strategy import LABEL, Strategy

pd.set_option("display.width", 200)
pd.set_option("display.max_columns", 30)


def _add_strategy_args(p: argparse.ArgumentParser) -> None:
    d = Strategy()
    g = p.add_argument_group("стратегия")
    g.add_argument("--strategy-file", help="JSON с настройками (например, из optimize)")
    g.add_argument("--markets", nargs="+", default=list(d.markets), choices=["1X2", "OU25"])
    g.add_argument("--min-edge", type=float, default=d.min_edge)
    g.add_argument("--min-odds", type=float, default=d.min_odds)
    g.add_argument("--max-odds", type=float, default=d.max_odds)
    g.add_argument("--min-prob", type=float, default=d.min_prob)
    g.add_argument("--market-weight", type=float, default=d.market_weight)
    g.add_argument("--staking", choices=["kelly", "flat"], default=d.staking)
    g.add_argument("--kelly-fraction", type=float, default=d.kelly_fraction)
    g.add_argument("--max-stake", type=float, default=d.max_stake)


def _strategy(args) -> Strategy:
    s = Strategy(markets=tuple(args.markets), min_edge=args.min_edge, min_odds=args.min_odds,
                 max_odds=args.max_odds, min_prob=args.min_prob, market_weight=args.market_weight,
                 staking=args.staking, kelly_fraction=args.kelly_fraction, max_stake=args.max_stake)
    if args.strategy_file:
        cfg = json.loads(Path(args.strategy_file).read_text())
        cfg["markets"] = tuple(cfg.get("markets", s.markets))
        s = replace(s, **{k: v for k, v in cfg.items() if k in s.to_dict()})
    return s


def _add_data_args(p: argparse.ArgumentParser, default_seasons: int) -> None:
    p.add_argument("--leagues", nargs="+", default=["E0"], help="коды лиг, см. команду leagues")
    p.add_argument("--seasons", nargs="+", help="коды сезонов, например 2223 2324 2425")
    p.add_argument("--last", type=int, default=default_seasons, help="или N последних сезонов")
    p.add_argument("--csv", nargs="+", help="локальные CSV football-data вместо скачивания")
    p.add_argument("--demo", action="store_true", help="синтетические данные без интернета")


def _load(args) -> pd.DataFrame:
    if args.demo:
        from .synthetic import make_league
        return make_league(n_seasons=max(args.last, 2))
    if args.csv:
        return data.load_local(args.csv)
    seasons = args.seasons or data.previous_seasons(args.last)
    print(f"Загрузка: лиги {', '.join(args.leagues)}; сезоны {', '.join(seasons)}")
    return data.load_history(args.leagues, seasons)


def _print_summary(title: str, s: dict) -> None:
    print(f"\n=== {title} ===")
    if not s.get("bets"):
        print("Ставок нет — ослабьте фильтры.")
        return
    print(f"Ставок:               {s['bets']}")
    print(f"Угадано:              {s['hit_rate']:.1%}   (средний кэф {s['avg_odds']:.2f})")
    print(f"Прибыль (по 1 ед.):   {s['profit_units']:+.1f} ед.   ROI {s['roi']:+.1%}")
    print(f"Макс. просадка:       {s['max_dd_units']:.1f} ед.   серия проигрышей до {s['losing_streak']}")
    print(f"Надёжность (t-стат.): {s['t_stat']:.2f}   (>2 — прибыль вряд ли случайна)")
    print(f"Банк 1000 → {s['final_bank']:.0f}  ({s['bank_growth']:+.1%}), "
          f"макс. просадка банка {s['max_dd_bank']:.1%}")


def cmd_leagues(args) -> None:
    for code, name in data.LEAGUES.items():
        print(f"{code:4s} {name}")


def cmd_backtest(args) -> None:
    matches = _load(args)
    print(f"Матчей: {len(matches)}. Бэктест по ходу времени...")
    sel = walk_forward(matches, refit_days=args.refit_days)
    if args.save:
        sel.to_csv(args.save, index=False)
        print(f"Прогнозы сохранены: {args.save}")
    strat = _strategy(args)
    bets = simulate(sel, strat)
    _print_summary("Итог стратегии", summarize(bets))
    if not bets.empty:
        bets["label"] = bets["selection"].map(LABEL)
        bets["odds_bucket"] = pd.cut(bets["price"], [1, 1.5, 2, 2.5, 3, 4, 100])
        for by, name in (("label", "по типу ставки"), ("odds_bucket", "по коэффициенту"),
                         ("league", "по лигам")):
            print(f"\n--- {name} ---")
            print(breakdown(bets, by).to_string(float_format=lambda x: f"{x:.3f}"))
        if args.bets_out:
            bets.to_csv(args.bets_out, index=False)
            print(f"\nВсе ставки: {args.bets_out}")


def cmd_optimize(args) -> None:
    if args.selections:
        sel = pd.read_csv(args.selections, parse_dates=["date"])
    else:
        matches = _load(args)
        print(f"Матчей: {len(matches)}. Бэктест по ходу времени...")
        sel = walk_forward(matches, refit_days=args.refit_days)
    holdout = args.holdout_from
    if holdout is None:
        # по умолчанию проверяем на последней трети периода
        dates = sel["date"].sort_values()
        holdout = dates.iloc[int(len(dates) * 2 / 3)]
    print(f"Подбор на данных до {pd.Timestamp(holdout).date()}, проверка — после.")
    res = optimize(sel, min_bets=args.min_bets, holdout_from=holdout)
    if res.empty:
        print("Ни одна комбинация не набрала достаточно ставок.")
        return
    cols = ["markets", "min_edge", "odds_range", "market_weight", "min_prob",
            "train_bets", "train_roi", "train_max_dd_units", "train_t_stat",
            "test_bets", "test_roi", "test_max_dd_units", "test_t_stat"]
    print("\nЛучшие настройки (сортировка по надёжности прибыли на обучении):")
    print(res[cols].head(args.top).to_string(float_format=lambda x: f"{x:.3f}"))
    # лучшая = та, у которой надёжность высокая И на обучении, И на проверке
    res["score"] = res[["train_t_stat", "test_t_stat"]].min(axis=1)
    robust = res[(res["test_roi"] > 0) & (res["train_roi"] > 0)]
    best = (robust if not robust.empty else res).sort_values("score", ascending=False).iloc[0]
    cfg = {"markets": list(best["markets"]), "min_edge": best["min_edge"],
           "min_odds": best["odds_range"][0], "max_odds": best["odds_range"][1],
           "market_weight": best["market_weight"], "min_prob": best["min_prob"]}
    Path(args.out).write_text(json.dumps(cfg, indent=2, ensure_ascii=False))
    print(f"\nЛучшая стратегия сохранена в {args.out}:\n{json.dumps(cfg, ensure_ascii=False)}")
    if robust.empty:
        print("ВНИМАНИЕ: ни одна настройка не была прибыльной и на обучении, и на проверке. "
              "Ставить по этим данным не рекомендуется.")


def cmd_predict(args) -> None:
    strat = _strategy(args)
    history = _load(args)
    if args.demo:
        last = history["date"].max()
        fixtures = history[history["date"] == last].copy()
        history = history[history["date"] < last]
    elif args.fixtures:
        fixtures = data.load_local([args.fixtures])
    else:
        fixtures = data.load_fixtures(args.leagues)
    bets, n = upcoming_bets(history, fixtures, strat)
    if n == 0:
        print("Нет ближайших матчей с коэффициентами для выбранных лиг.")
        return
    print(f"\nПроанализировано матчей: {n}")
    if bets.empty:
        print("Подходящих ставок нет — это нормально: лучше пропустить, чем играть без перевеса.")
        return
    bets["ставка"] = bets["selection"].map(LABEL)
    bets["% банка"] = (bets["stake_frac"] * 100).round(2)
    view = bets[["date", "league", "home", "away", "ставка", "price", "p", "market_p", "edge", "% банка"]]
    view = view.rename(columns={"date": "дата", "league": "лига", "home": "хозяева", "away": "гости",
                                "price": "кэф", "p": "вероятность", "market_p": "рынок", "edge": "перевес"})
    view["дата"] = view["дата"].dt.strftime("%d.%m")
    print(view.to_string(index=False, float_format=lambda x: f"{x:.3f}"))


def _env(name: str, required: bool = True) -> str | None:
    val = os.environ.get(name, "").strip()
    if required and not val:
        sys.exit(f"Не задана переменная окружения {name}")
    return val or None


def cmd_notify(args) -> None:
    """Разовая отправка в Telegram (для GitHub Actions по расписанию)."""
    from .telegram import send
    if not args.dry_run:
        token, chat = _env("TELEGRAM_BOT_TOKEN"), _env("TELEGRAM_CHAT_ID")
    leagues = [x.upper() for x in args.leagues]
    if args.kind == "report":
        text = weekly_report(leagues, args.last, args.strategy_file)
    else:
        strat, cfg = load_strategy(args.strategy_file)
        bets, n = fetch_upcoming(leagues, strat)
        text = tips_message(bets, n, cfg)
    print(text)
    if not args.dry_run:
        for c in chat.split(","):
            send(token, c.strip(), text)


def _demo_journal(history: pd.DataFrame) -> str:
    """Журнал для демо: ставки на несколько прошлых туров с уже известными результатами."""
    import tempfile
    from . import journal as jr
    from .service import analyze_fixtures
    path = Path(tempfile.gettempdir()) / "match_analyzer_demo_journal.csv"
    path.unlink(missing_ok=True)
    j = jr.load(path)
    strat = Strategy(min_edge=0.03, min_prob=0.0)
    for day in sorted(history["date"].unique())[-8:]:
        past, rnd = history[history["date"] < day], history[history["date"] == day].copy()
        rnd["time"] = "15:00"
        sel = analyze_fixtures(past, rnd.assign(hg=float("nan"), ag=float("nan")))
        j = jr.add_bets(j, strat.select(sel), now=pd.Timestamp(day, tz="Europe/London") - pd.Timedelta(days=1))
    j = jr.settle(j, history)
    jr.save(j, path)
    return str(path)


def cmd_site(args) -> None:
    """Собирает веб-приложение (папка со страницей и свежим data.json)."""
    from .webapp import build_site
    leagues = [x.upper() for x in args.leagues]
    history = fixtures = None
    if args.demo:
        from .synthetic import make_league
        history = make_league(n_seasons=3)
        days = sorted(history["date"].unique())[-2:]
        fixtures = history[history["date"].isin(days)].copy()
        history = history[history["date"] < days[0]]
        today = pd.Timestamp.now().normalize()
        fixtures["date"] = fixtures["date"].map({days[0]: today, days[1]: today + pd.Timedelta(days=1)})
        fixtures["time"] = "19:30"
        fixtures[["hg", "ag"]] = float("nan")
        args.journal = _demo_journal(history)
    payload = build_site(args.out, leagues, args.strategy_file, history=history, fixtures=fixtures,
                         journal_path=args.journal)
    n_bets = sum(1 for m in payload["matches"] if m["bet"])
    print(f"Приложение собрано в {args.out}: матчей {len(payload['matches'])}, ставок {n_bets}")
    if args.telegram:
        token, chat = os.environ.get("TELEGRAM_BOT_TOKEN"), os.environ.get("TELEGRAM_CHAT_ID")
        if token and chat:
            from .telegram import send
            sel_bets = [m for m in payload["matches"] if m["bet"]]
            text = f"⚽ Ставок на ближайшие матчи: {len(sel_bets)}"
            if args.app_url:
                text += f"\n{args.app_url}"
            for c in chat.split(","):
                send(token, c.strip(), text)


def cmd_bot(args) -> None:
    from .telegram import run_bot
    allowed = _env("TELEGRAM_ALLOWED_CHATS", required=False)
    run_bot(_env("TELEGRAM_BOT_TOKEN"), [x.upper() for x in args.leagues], args.strategy_file,
            set(allowed.split(",")) if allowed else None)


def main(argv=None) -> None:
    ap = argparse.ArgumentParser(prog="match_analyzer", description="Анализ футбольных матчей и поиск value-ставок")
    sub = ap.add_subparsers(dest="cmd", required=True)

    sub.add_parser("leagues", help="список лиг").set_defaults(func=cmd_leagues)

    p = sub.add_parser("backtest", help="проверить стратегию на истории")
    _add_data_args(p, 5)
    _add_strategy_args(p)
    p.add_argument("--refit-days", type=int, default=7)
    p.add_argument("--save", help="сохранить прогнозы в CSV (для быстрого optimize)")
    p.add_argument("--bets-out", help="сохранить список ставок в CSV")
    p.set_defaults(func=cmd_backtest)

    p = sub.add_parser("optimize", help="подобрать самые прибыльные и надёжные настройки")
    _add_data_args(p, 6)
    p.add_argument("--selections", help="CSV прогнозов из backtest --save")
    p.add_argument("--refit-days", type=int, default=7)
    p.add_argument("--holdout-from", help="дата начала проверочного периода (ГГГГ-ММ-ДД)")
    p.add_argument("--min-bets", type=int, default=100)
    p.add_argument("--top", type=int, default=15)
    p.add_argument("--out", default="best_strategy.json")
    p.set_defaults(func=cmd_optimize)

    p = sub.add_parser("predict", help="ставки на ближайшие матчи")
    _add_data_args(p, 3)
    _add_strategy_args(p)
    p.add_argument("--fixtures", help="локальный fixtures.csv вместо скачивания")
    p.set_defaults(func=cmd_predict)

    p = sub.add_parser("notify", help="отправить ставки или отчёт в Telegram")
    p.add_argument("kind", choices=["tips", "report"])
    p.add_argument("--leagues", nargs="+", default=list(data.LEAGUES))
    p.add_argument("--last", type=int, default=10, help="сезонов для отчёта")
    p.add_argument("--strategy-file", default="strategy/best_strategy.json")
    p.add_argument("--dry-run", action="store_true", help="только напечатать, не отправлять")
    p.set_defaults(func=cmd_notify)

    p = sub.add_parser("site", help="собрать веб-приложение")
    p.add_argument("--leagues", nargs="+", default=list(data.LEAGUES))
    p.add_argument("--strategy-file", default="strategy/best_strategy.json")
    p.add_argument("--out", default="_site")
    p.add_argument("--journal", default="journal/bets.csv", help="CSV журнала ставок")
    p.add_argument("--demo", action="store_true", help="демо-данные без интернета")
    p.add_argument("--telegram", action="store_true", help="если заданы секреты — короткое уведомление")
    p.add_argument("--app-url", help="ссылка на приложение для уведомления")
    p.set_defaults(func=cmd_site)

    p = sub.add_parser("bot", help="интерактивный Telegram-бот (нужен сервер)")
    p.add_argument("--leagues", nargs="+", default=list(data.LEAGUES))
    p.add_argument("--strategy-file", default="strategy/best_strategy.json")
    p.set_defaults(func=cmd_bot)

    args = ap.parse_args(argv)
    args.func(args)


if __name__ == "__main__":
    main(sys.argv[1:])
