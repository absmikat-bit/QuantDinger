#!/usr/bin/env python3
"""Standalone M1 backtester for the supplied MT5 Time/TP/SL rules."""

from __future__ import annotations

import argparse
import csv
import json
import math
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

WEEKDAYS = {
    "monday": 0,
    "tuesday": 1,
    "wednesday": 2,
    "thursday": 3,
    "friday": 4,
    "saturday": 5,
    "sunday": 6,
}
REQUIRED_COLUMNS = ("datetime", "open", "high", "low", "close")


@dataclass(frozen=True)
class Bar:
    datetime: datetime
    open: float
    high: float
    low: float
    close: float


@dataclass(frozen=True)
class Config:
    symbol: str
    direction: str
    signal_hour: int
    signal_minute: int
    take_profit_pips: float
    stop_loss_pips: float
    pip_size: float
    trading_days: tuple[str, ...] = tuple(WEEKDAYS)
    same_bar_rule: int = 0
    max_hold_bars: int = 0
    initial_balance: float = 25000.0
    dd_risk_percent: float = 1.0
    min_resolved_trades: int = 30

    @classmethod
    def from_dict(cls, values: dict[str, Any]) -> Config:
        data = dict(values)
        if "trading_days" in data:
            data["trading_days"] = tuple(data["trading_days"])
        return cls(**data)

    def __post_init__(self) -> None:
        direction = self.direction.upper()
        object.__setattr__(self, "direction", direction)
        if not self.symbol.strip():
            raise ValueError("symbol must not be empty")
        if direction not in {"BUY", "SELL"}:
            raise ValueError("direction must be BUY or SELL")
        if not 0 <= self.signal_hour <= 23:
            raise ValueError("signal_hour must be between 0 and 23")
        if not 0 <= self.signal_minute <= 59:
            raise ValueError("signal_minute must be between 0 and 59")
        if self.take_profit_pips <= 0 or self.stop_loss_pips <= 0:
            raise ValueError("take_profit_pips and stop_loss_pips must be positive")
        if self.pip_size <= 0:
            raise ValueError("pip_size must be positive")
        if self.same_bar_rule not in (0, 1):
            raise ValueError("same_bar_rule must be 0 (SL first) or 1 (TP first)")
        if self.max_hold_bars < 0:
            raise ValueError("max_hold_bars must be zero or positive")
        if self.initial_balance <= 0:
            raise ValueError("initial_balance must be positive")
        if self.dd_risk_percent < 0:
            raise ValueError("dd_risk_percent must not be negative")
        if self.min_resolved_trades < 0:
            raise ValueError("min_resolved_trades must not be negative")
        invalid_days = [day for day in self.trading_days if day.lower() not in WEEKDAYS]
        if invalid_days:
            raise ValueError(f"Unknown trading day(s): {', '.join(invalid_days)}")

    @property
    def enabled_weekdays(self) -> frozenset[int]:
        return frozenset(WEEKDAYS[day.lower()] for day in self.trading_days)


@dataclass
class Trade:
    signal_time: datetime
    entry: float
    take_profit: float
    stop_loss: float
    outcome: str = "PENDING"
    exit_time: datetime | None = None
    age_bars: int = 0


@dataclass(frozen=True)
class BacktestResult:
    symbol: str
    direction: str
    signals: int
    wins: int
    losses: int
    pending: int
    resolved: int
    win_rate: float
    win_r: float
    net_r: float
    profit_factor: float | None
    max_consecutive_sl: int
    max_sl_calendar_days: int
    max_dd_r: float
    max_dd_dollars: float
    max_dd_percent: float
    worst_equity: float


def parse_datetime(value: str) -> datetime:
    normalized = value.strip().replace("Z", "+00:00")
    try:
        return datetime.fromisoformat(normalized)
    except ValueError:
        for pattern in ("%Y.%m.%d %H:%M", "%Y.%m.%d %H:%M:%S"):
            try:
                return datetime.strptime(value.strip(), pattern)
            except ValueError:
                continue
    raise ValueError(f"Unsupported datetime value: {value!r}")


def load_bars(csv_path: str | Path) -> list[Bar]:
    bars: list[Bar] = []
    with Path(csv_path).open(newline="", encoding="utf-8-sig") as stream:
        first_line = stream.readline()
        if not first_line:
            return bars
        native_header = [column.strip().strip("<>").upper() for column in first_line.split()]
        is_mt5_export = "DATE" in native_header and "TIME" in native_header

        if is_mt5_export:
            required_mt5 = ("DATE", "TIME", "OPEN", "HIGH", "LOW", "CLOSE")
            missing = [column for column in required_mt5 if column not in native_header]
            if missing:
                raise ValueError(f"MT5 export is missing required column(s): {', '.join(missing)}")
            indexes = {column: native_header.index(column) for column in required_mt5}
            rows = (
                (line_number, line.split(), None)
                for line_number, line in enumerate(stream, start=2)
                if line.strip()
            )
        else:
            reader = csv.DictReader([first_line, *stream])
            missing = [column for column in REQUIRED_COLUMNS if column not in (reader.fieldnames or [])]
            if missing:
                raise ValueError(f"CSV is missing required column(s): {', '.join(missing)}")
            indexes = {}
            rows = (
                (line_number, None, row)
                for line_number, row in enumerate(reader, start=2)
            )

        for line_number, fields, row in rows:
            try:
                if is_mt5_export:
                    assert fields is not None
                    if len(fields) <= max(indexes.values()):
                        raise ValueError("row has fewer fields than the MT5 header")
                    timestamp = f"{fields[indexes['DATE']]} {fields[indexes['TIME']]}"
                    prices = {
                        column.lower(): fields[indexes[column]]
                        for column in ("OPEN", "HIGH", "LOW", "CLOSE")
                    }
                else:
                    assert row is not None
                    timestamp = row["datetime"]
                    prices = row
                bar = Bar(
                    datetime=parse_datetime(timestamp),
                    open=float(prices["open"]),
                    high=float(prices["high"]),
                    low=float(prices["low"]),
                    close=float(prices["close"]),
                )
            except (TypeError, ValueError) as exc:
                raise ValueError(f"Invalid CSV data on line {line_number}: {exc}") from exc
            if not all(math.isfinite(price) for price in (bar.open, bar.high, bar.low, bar.close)):
                raise ValueError(f"Non-finite OHLC value on line {line_number}")
            if bar.high < max(bar.open, bar.close, bar.low) or bar.low > min(
                bar.open, bar.close, bar.high
            ):
                raise ValueError(f"Inconsistent OHLC values on line {line_number}")
            if bars and bar.datetime <= bars[-1].datetime:
                raise ValueError(f"CSV datetimes must be strictly increasing (line {line_number})")
            bars.append(bar)
    return bars


def _bar_hits(bar: Bar, config: Config, trade: Trade) -> tuple[bool, bool]:
    if config.direction == "BUY":
        return bar.high >= trade.take_profit, bar.low <= trade.stop_loss
    return bar.low <= trade.take_profit, bar.high >= trade.stop_loss


def _drawdown(closed_trades: list[Trade], config: Config) -> tuple[float, float, float, float]:
    risk_money = config.initial_balance * config.dd_risk_percent / 100.0
    equity = peak_equity = config.initial_balance
    max_dd_dollars = 0.0
    max_dd_percent = 0.0
    worst_equity = equity
    win_r = config.take_profit_pips / config.stop_loss_pips

    # Match MT5: process resolved trades in signal order.
    for trade in sorted(closed_trades, key=lambda item: item.signal_time):
        if trade.outcome == "WIN":
            equity += risk_money * win_r
        elif trade.outcome == "LOSS":
            equity -= risk_money

        if equity > peak_equity:
            peak_equity = equity

        dd_dollars = peak_equity - equity

        # Match MT5's maximum-drawdown update rule.
        if dd_dollars > max_dd_dollars + 0.0000001:
            max_dd_dollars = dd_dollars
            max_dd_percent = (
                dd_dollars / peak_equity * 100.0
                if peak_equity > 0.0 else 0.0
            )
            worst_equity = equity

    max_dd_r = max_dd_dollars / risk_money if risk_money else 0.0
    return max_dd_r, max_dd_dollars, max_dd_percent, worst_equity


def run_backtest(bars: list[Bar], config: Config) -> tuple[BacktestResult, list[Trade]]:
    open_trades: list[Trade] = []
    trades: list[Trade] = []
    enabled_days = config.enabled_weekdays

    for bar in bars:
        still_open: list[Trade] = []
        for trade in open_trades:
            trade.age_bars += 1
            hit_tp, hit_sl = _bar_hits(bar, config, trade)
            if hit_tp and hit_sl:
                trade.outcome = "WIN" if config.same_bar_rule == 1 else "LOSS"
                trade.exit_time = bar.datetime
            elif hit_tp:
                trade.outcome = "WIN"
                trade.exit_time = bar.datetime
            elif hit_sl:
                trade.outcome = "LOSS"
                trade.exit_time = bar.datetime
            elif config.max_hold_bars and trade.age_bars >= config.max_hold_bars:
                trade.outcome = "PENDING"
                trade.exit_time = bar.datetime
            else:
                still_open.append(trade)
        open_trades = still_open

        if (
            bar.datetime.hour == config.signal_hour
            and bar.datetime.minute == config.signal_minute
            and bar.datetime.weekday() in enabled_days
        ):
            entry = bar.close
            if config.direction == "BUY":
                tp = entry + config.take_profit_pips * config.pip_size
                sl = entry - config.stop_loss_pips * config.pip_size
            else:
                tp = entry - config.take_profit_pips * config.pip_size
                sl = entry + config.stop_loss_pips * config.pip_size
            trade = Trade(bar.datetime, entry, tp, sl)
            trades.append(trade)
            open_trades.append(trade)

    wins = sum(trade.outcome == "WIN" for trade in trades)
    losses = sum(trade.outcome == "LOSS" for trade in trades)
    pending = len(trades) - wins - losses
    resolved = wins + losses
    win_r = config.take_profit_pips / config.stop_loss_pips
    net_r = wins * win_r - losses
    profit_factor = (
        (wins * config.take_profit_pips) / (losses * config.stop_loss_pips)
        if losses
        else (math.inf if wins else None)
    )

    max_streak = current_streak = 0
    max_streak_start: datetime | None = None
    max_streak_end: datetime | None = None
    streak_start: datetime | None = None
    for trade in trades:
        if trade.outcome == "LOSS":
            if current_streak == 0:
                streak_start = trade.signal_time
            current_streak += 1
            if current_streak > max_streak:
                max_streak = current_streak
                max_streak_start = streak_start
                max_streak_end = trade.signal_time
        else:
            current_streak = 0
            streak_start = None
    max_sl_calendar_days = (
        (max_streak_end.date() - max_streak_start.date()).days + 1
        if max_streak_start and max_streak_end
        else 0
    )
    max_dd_r, max_dd_dollars, max_dd_percent, worst_equity = _drawdown(
        [trade for trade in trades if trade.outcome in {"WIN", "LOSS"}], config
    )
    result = BacktestResult(
        symbol=config.symbol,
        direction=config.direction,
        signals=len(trades),
        wins=wins,
        losses=losses,
        pending=pending,
        resolved=resolved,
        win_rate=(wins / resolved * 100.0) if resolved else 0.0,
        win_r=win_r,
        net_r=net_r,
        profit_factor=profit_factor,
        max_consecutive_sl=max_streak,
        max_sl_calendar_days=max_sl_calendar_days,
        max_dd_r=max_dd_r,
        max_dd_dollars=max_dd_dollars,
        max_dd_percent=max_dd_percent,
        worst_equity=worst_equity,
    )
    return result, trades


def format_result(result: BacktestResult) -> str:
    factor = "N/A" if result.profit_factor is None else (
        "inf" if math.isinf(result.profit_factor) else f"{result.profit_factor:.4f}"
    )
    return "\n".join(
        (
            f"Symbol: {result.symbol}",
            f"Direction: {result.direction}",
            f"Signals: {result.signals}",
            f"Wins: {result.wins}",
            f"Losses: {result.losses}",
            f"Pending: {result.pending}",
            f"Win Rate: {result.win_rate:.2f}%",
            f"Net R: {result.net_r:.4f}",
            f"Profit Factor: {factor}",
            f"Max Consecutive SL: {result.max_consecutive_sl}",
            f"Max SL Calendar Days: {result.max_sl_calendar_days}",
            f"Max DD R: {result.max_dd_r:.4f}",
            f"Max DD $: {result.max_dd_dollars:.2f}",
            f"Max DD %: {result.max_dd_percent:.4f}%",
            f"Worst Equity: {result.worst_equity:.2f}",
        )
    )


def load_config(path: str | Path) -> Config:
    with Path(path).open(encoding="utf-8") as stream:
        return Config.from_dict(json.load(stream))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", default="config.json", help="Path to JSON configuration")
    parser.add_argument("--csv", dest="csv_path", help="Override the configured M1 CSV path")
    args = parser.parse_args()
    with Path(args.config).open(encoding="utf-8") as stream:
        settings = json.load(stream)
    config = Config.from_dict(settings["backtest"])
    csv_path = args.csv_path or settings["data_file"]
    result, _ = run_backtest(load_bars(csv_path), config)
    print(format_result(result))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
