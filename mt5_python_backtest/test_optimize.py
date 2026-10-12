from __future__ import annotations

import unittest

from backtest import BacktestResult
from optimize import parse_range, rank_key


def result(*, streak: int, win_rate: float, net_r: float, factor: float) -> BacktestResult:
    return BacktestResult(
        symbol="TEST",
        direction="BUY",
        signals=40,
        wins=20,
        losses=20,
        pending=0,
        resolved=40,
        win_rate=win_rate,
        win_r=1.0,
        net_r=net_r,
        profit_factor=factor,
        max_consecutive_sl=streak,
        max_sl_calendar_days=1,
        max_dd_r=0,
        max_dd_dollars=0,
        max_dd_percent=0,
        worst_equity=25000,
    )


class OptimizerTests(unittest.TestCase):
    def test_grid_range_is_inclusive(self) -> None:
        self.assertEqual(parse_range("10:16:3"), [10, 13, 16])

    def test_ranking_prioritizes_streak_before_other_metrics(self) -> None:
        lower_streak = result(streak=2, win_rate=40, net_r=1, factor=1.1)
        higher_streak = result(streak=3, win_rate=90, net_r=50, factor=5)
        self.assertLess(rank_key(lower_streak), rank_key(higher_streak))

    def test_ranking_uses_win_rate_then_net_r_then_profit_factor(self) -> None:
        baseline = result(streak=2, win_rate=50, net_r=2, factor=1.2)
        by_win_rate = result(streak=2, win_rate=60, net_r=0, factor=1.0)
        by_net_r = result(streak=2, win_rate=50, net_r=3, factor=1.0)
        by_factor = result(streak=2, win_rate=50, net_r=2, factor=1.3)
        self.assertLess(rank_key(by_win_rate), rank_key(baseline))
        self.assertLess(rank_key(by_net_r), rank_key(baseline))
        self.assertLess(rank_key(by_factor), rank_key(baseline))


if __name__ == "__main__":
    unittest.main()
