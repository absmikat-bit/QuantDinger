from __future__ import annotations

import json
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path

from backtest import Bar, Config, load_bars, run_backtest


def bar(minute: int, *, open_: float = 100, high: float = 100, low: float = 100, close: float = 100) -> Bar:
    return Bar(datetime(2024, 1, 1, 18, minute), open_, high, low, close)


def config(direction: str = "BUY", **overrides: object) -> Config:
    values = {
        "symbol": "TEST",
        "direction": direction,
        "signal_hour": 18,
        "signal_minute": 0,
        "take_profit_pips": 2,
        "stop_loss_pips": 2,
        "pip_size": 1,
        "trading_days": ("Monday",),
        "same_bar_rule": 0,
        "max_hold_bars": 0,
        "initial_balance": 25000,
        "dd_risk_percent": 1.0,
        "min_resolved_trades": 30,
    }
    values.update(overrides)
    return Config(**values)  # type: ignore[arg-type]


class BacktestTests(unittest.TestCase):
    def test_loads_native_mt5_export_bars_format(self) -> None:
        contents = (
            "<DATE>\t<TIME>\t<OPEN>\t<HIGH>\t<LOW>\t<CLOSE>\t"
            "<TICKVOL>\t<VOL>\t<SPREAD>\n"
            "2022.08.29\t01:00:00\t1.17000\t1.17120\t1.16950\t1.17080\t"
            "123\t0\t15\n"
        )
        with tempfile.TemporaryDirectory() as directory:
            csv_path = Path(directory) / "mt5_export.csv"
            csv_path.write_text(contents, encoding="utf-8")
            bars = load_bars(csv_path)

        self.assertEqual(len(bars), 1)
        self.assertEqual(bars[0], Bar(datetime(2022, 8, 29, 1, 0), 1.17, 1.1712, 1.1695, 1.1708))

    def test_load_bars_keeps_existing_comma_csv_support(self) -> None:
        contents = "datetime,open,high,low,close\n2022-08-29 01:00:00,1.17,1.1712,1.1695,1.1708\n"
        with tempfile.TemporaryDirectory() as directory:
            csv_path = Path(directory) / "bars.csv"
            csv_path.write_text(contents, encoding="utf-8")
            bars = load_bars(csv_path)

        self.assertEqual(len(bars), 1)
        self.assertEqual(bars[0].datetime, datetime(2022, 8, 29, 1, 0))

    def test_requested_fixed_validation_configuration(self) -> None:
        settings = json.loads(Path("config.json").read_text(encoding="utf-8"))
        fixed = Config.from_dict(settings["backtest"])
        self.assertEqual(
            (
                fixed.direction,
                fixed.signal_hour,
                fixed.signal_minute,
                fixed.take_profit_pips,
                fixed.stop_loss_pips,
                fixed.pip_size,
                fixed.initial_balance,
                fixed.dd_risk_percent,
                fixed.same_bar_rule,
                fixed.max_hold_bars,
            ),
            ("BUY", 18, 10, 55, 68, 0.1, 25000, 1.0, 0, 0),
        )
        signal = Bar(datetime(2024, 1, 1, 18, 10), 100, 100, 100, 100)
        tp_bar = Bar(datetime(2024, 1, 1, 18, 11), 100, 105.5, 100, 100)
        result, _ = run_backtest([signal, tp_bar], fixed)
        self.assertEqual((result.wins, result.losses, result.pending), (1, 0, 0))

    def test_buy_tp(self) -> None:
        result, _ = run_backtest([bar(0), bar(1, high=102)], config())
        self.assertEqual((result.wins, result.losses), (1, 0))

    def test_buy_sl(self) -> None:
        result, _ = run_backtest([bar(0), bar(1, low=98)], config())
        self.assertEqual((result.wins, result.losses), (0, 1))

    def test_sell_tp(self) -> None:
        result, _ = run_backtest([bar(0), bar(1, low=98)], config("SELL"))
        self.assertEqual((result.wins, result.losses), (1, 0))

    def test_sell_sl(self) -> None:
        result, _ = run_backtest([bar(0), bar(1, high=102)], config("SELL"))
        self.assertEqual((result.wins, result.losses), (0, 1))

    def test_same_bar_rule_selects_sl_or_tp(self) -> None:
        bars = [bar(0), bar(1, high=102, low=98)]
        sl_first, _ = run_backtest(bars, config(same_bar_rule=0))
        tp_first, _ = run_backtest(bars, config(same_bar_rule=1))
        self.assertEqual((sl_first.wins, sl_first.losses), (0, 1))
        self.assertEqual((tp_first.wins, tp_first.losses), (1, 0))

    def test_signal_candle_cannot_resolve_itself(self) -> None:
        result, trades = run_backtest([bar(0, high=110, low=90)], config())
        self.assertEqual(result.signals, 1)
        self.assertEqual(result.pending, 1)
        self.assertEqual(trades[0].age_bars, 0)

    def test_max_hold_expires_on_nth_unresolved_bar(self) -> None:
        result, trades = run_backtest(
            [bar(0), bar(1), bar(2), bar(3, high=102)],
            config(max_hold_bars=2),
        )
        self.assertEqual(result.pending, 1)
        self.assertEqual(trades[0].age_bars, 2)
        self.assertEqual(trades[0].exit_time, bar(2).datetime)

    def test_max_sl_streak_uses_signal_order_and_calendar_span(self) -> None:
        first = Bar(datetime(2024, 1, 1, 18, 0), 100, 100, 100, 100)
        later = Bar(datetime(2024, 1, 3, 18, 0), 100, 100, 100, 100)
        bars = [
            first,
            Bar(first.datetime + timedelta(minutes=1), 100, 100, 98, 100),
            later,
            Bar(later.datetime + timedelta(minutes=1), 100, 100, 98, 100),
        ]
        result, _ = run_backtest(
            bars,
            config(
                take_profit_pips=5,
                stop_loss_pips=2,
                trading_days=("Monday", "Wednesday"),
            ),
        )
        self.assertEqual(result.max_consecutive_sl, 2)
        self.assertEqual(result.max_sl_calendar_days, 3)

    def test_closed_trade_drawdown(self) -> None:
        start = datetime(2024, 1, 1, 18, 0)
        bars = []
        for day, high, low in ((0, 100, 98), (1, 100, 98), (2, 102, 100)):
            signal = Bar(start + timedelta(days=day), 100, 100, 100, 100)
            resolution = Bar(start + timedelta(days=day, minutes=1), 100, high, low, 100)
            bars.extend((signal, resolution))
        result, _ = run_backtest(
            bars,
            config(
                take_profit_pips=2,
                stop_loss_pips=2,
                dd_risk_percent=1,
                trading_days=("Monday", "Tuesday", "Wednesday"),
            ),
        )
        self.assertAlmostEqual(result.max_dd_dollars, 500)
        self.assertAlmostEqual(result.max_dd_r, 2)
        self.assertAlmostEqual(result.max_dd_percent, 2)
        self.assertAlmostEqual(result.worst_equity, 24500)


if __name__ == "__main__":
    unittest.main()
