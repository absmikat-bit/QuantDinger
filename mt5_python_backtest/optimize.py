#!/usr/bin/env python3
"""Grid optimizer with TP > SL filtering and live progress."""

from __future__ import annotations

import argparse
import csv
import itertools
import json
import math
import time
from dataclasses import replace
from pathlib import Path
from typing import Any

from backtest import BacktestResult, Config, format_result, load_bars, run_backtest


def range_values(spec: list[int | float]) -> list[int | float]:
    if len(spec) != 3:
        raise ValueError("Each range must be [start, end, step]")
    start, end, step = spec
    if step <= 0 or end < start:
        raise ValueError("Invalid range: expected [start, end, positive step]")
    values = []
    current = start
    while current <= end:
        values.append(current)
        current += step
    return values


def parse_range(value: str) -> list[int]:
    try:
        parts = [int(part) for part in value.split(":")]
    except ValueError as exc:
        raise argparse.ArgumentTypeError(
            "Range must use START:END[:STEP]"
        ) from exc

    if len(parts) == 2:
        parts.append(1)
    if len(parts) != 3 or parts[2] <= 0 or parts[1] < parts[0]:
        raise argparse.ArgumentTypeError(
            "Range must be ascending START:END[:positive STEP]"
        )
    return list(range(parts[0], parts[1] + 1, parts[2]))


def profit_factor_rank(result: BacktestResult) -> float:
    if result.profit_factor is None:
        return -1.0
    return result.profit_factor


def rank_key(result: BacktestResult) -> tuple[float, float, float, float]:
    # Primary: lowest consecutive SL streak.
    # Tie-breakers: highest WR, Net R, and Profit Factor.
    return (
        result.max_consecutive_sl,
        -result.win_rate,
        -result.net_r,
        -profit_factor_rank(result),
    )


def duration_text(seconds: float | None) -> str:
    if seconds is None or not math.isfinite(seconds):
        return "--"
    seconds = max(0, int(seconds))
    hours, remainder = divmod(seconds, 3600)
    minutes, secs = divmod(remainder, 60)
    if hours:
        return f"{hours:02d}:{minutes:02d}:{secs:02d}"
    return f"{minutes:02d}:{secs:02d}"


def show_progress(done: int, total: int, started: float) -> None:
    elapsed = time.perf_counter() - started
    fraction = done / total if total else 1.0
    percent = fraction * 100.0
    bar_width = 30
    filled = int(bar_width * fraction)
    bar = "#" * filled + "-" * (bar_width - filled)

    eta = elapsed * (total - done) / done if done else None
    print(
        f"\rProgress: [{bar}] {percent:6.2f}% | "
        f"{done:,}/{total:,} | "
        f"Elapsed {duration_text(elapsed)} | "
        f"ETA {duration_text(eta)}",
        end="",
        flush=True,
    )


def save_results(
    output_path: Path,
    results: list[tuple[int, int, float, float, BacktestResult]],
) -> None:
    columns = [
        "Rank", "SignalTime", "Direction", "TP", "SL", "RR",
        "Signals", "Wins", "Losses", "Pending", "Resolved",
        "WinRate", "NetR", "ProfitFactor",
        "MaxConsecutiveSL", "MaxSLCalendarDays",
        "MaxDD_R", "MaxDD_Dollars", "MaxDD_Percent", "WorstEquity",
    ]

    with output_path.open("w", newline="", encoding="utf-8-sig") as stream:
        writer = csv.DictWriter(stream, fieldnames=columns)
        writer.writeheader()

        for rank, (hour, minute, tp, sl, result) in enumerate(results, start=1):
            writer.writerow({
                "Rank": rank,
                "SignalTime": f"{hour:02d}:{minute:02d}",
                "Direction": result.direction,
                "TP": tp,
                "SL": sl,
                "RR": f"{tp / sl:.4f}" if sl else "",
                "Signals": result.signals,
                "Wins": result.wins,
                "Losses": result.losses,
                "Pending": result.pending,
                "Resolved": result.resolved,
                "WinRate": f"{result.win_rate:.4f}",
                "NetR": f"{result.net_r:.4f}",
                "ProfitFactor": (
                    "" if result.profit_factor is None
                    else f"{result.profit_factor:.4f}"
                ),
                "MaxConsecutiveSL": result.max_consecutive_sl,
                "MaxSLCalendarDays": result.max_sl_calendar_days,
                "MaxDD_R": f"{result.max_dd_r:.4f}",
                "MaxDD_Dollars": f"{result.max_dd_dollars:.2f}",
                "MaxDD_Percent": f"{result.max_dd_percent:.4f}",
                "WorstEquity": f"{result.worst_equity:.2f}",
            })


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", default="config.json")
    parser.add_argument("--csv", dest="csv_path", help="Override M1 CSV path")
    parser.add_argument("--hours", type=parse_range, help="Example: 0:23:1")
    parser.add_argument("--minutes", type=parse_range, help="Example: 0:59:1")
    parser.add_argument("--tp", type=parse_range, help="Example: 50:100:5")
    parser.add_argument("--sl", type=parse_range, help="Example: 50:100:5")
    parser.add_argument("--directions", help="BUY, SELL, or BUY,SELL")
    parser.add_argument("--top", type=int)
    parser.add_argument(
        "--output", default="optimization_results.csv",
        help="Output CSV filename",
    )
    args = parser.parse_args()

    with Path(args.config).open(encoding="utf-8") as stream:
        settings: dict[str, Any] = json.load(stream)

    base = Config.from_dict(settings["backtest"])
    optimizer = settings["optimization"]

    hours = args.hours or [
        int(v) for v in range_values(optimizer["signal_hours"])
    ]
    minutes = args.minutes or [
        int(v) for v in range_values(optimizer["signal_minutes"])
    ]
    take_profits = args.tp or range_values(optimizer["take_profit_pips"])
    stop_losses = args.sl or range_values(optimizer["stop_loss_pips"])

    directions = (
        [v.strip().upper() for v in args.directions.split(",")]
        if args.directions
        else [v.upper() for v in optimizer["directions"]]
    )
    top_count = args.top if args.top is not None else int(optimizer.get("top", 20))

    if top_count < 1:
        parser.error("--top must be positive")
    if not directions or any(v not in {"BUY", "SELL"} for v in directions):
        parser.error("Directions must contain BUY and/or SELL")
    if not hours or not minutes or not take_profits or not stop_losses:
        parser.error("Optimization ranges must not be empty")

    # Only keep TP/SL pairs where TP is STRICTLY greater than SL.
    valid_pairs = [
        (float(tp), float(sl))
        for tp, sl in itertools.product(take_profits, stop_losses)
        if float(tp) > float(sl)
    ]
    if not valid_pairs:
        parser.error("No valid TP/SL pairs: TP must be greater than SL")

    total = len(hours) * len(minutes) * len(valid_pairs) * len(directions)
    print(f"Data file: {args.csv_path or settings['data_file']}")
    print(f"Hours: {len(hours)} | Minutes: {len(minutes)}")
    print(f"Valid TP/SL pairs (TP > SL): {len(valid_pairs)}")
    print(f"Direction(s): {', '.join(directions)}")
    print(f"Total configurations: {total:,}")
    print("Loading M1 data...")

    bars = load_bars(args.csv_path or settings["data_file"])
    print(f"Loaded {len(bars):,} M1 bars.")
    print("Optimization started. Progress updates while configurations finish.")

    results: list[tuple[int, int, float, float, BacktestResult]] = []
    started = time.perf_counter()
    last_update = 0.0
    completed = 0
    output_path = Path(args.output)

    show_progress(0, total, started)

    try:
        for hour, minute, (tp, sl), direction in itertools.product(
            hours, minutes, valid_pairs, directions
        ):
            candidate = replace(
                base,
                signal_hour=int(hour),
                signal_minute=int(minute),
                take_profit_pips=tp,
                stop_loss_pips=sl,
                direction=direction,
            )

            result, _ = run_backtest(bars, candidate)
            completed += 1

            if result.resolved >= candidate.min_resolved_trades:
                results.append(
                    (int(hour), int(minute), tp, sl, result)
                )

            now = time.perf_counter()
            if now - last_update >= 0.5 or completed == total:
                show_progress(completed, total, started)
                last_update = now

    except KeyboardInterrupt:
        print("\nOptimization interrupted by user.")
        print(f"Completed {completed:,} of {total:,} ({completed / total * 100:.2f}%).")
        results.sort(key=lambda item: rank_key(item[4]))
        save_results(output_path, results)
        print(f"Partial eligible results saved to: {output_path.resolve()}")
        return 130

    print()
    results.sort(key=lambda item: rank_key(item[4]))
    save_results(output_path, results)

    print(f"Completed configurations: {completed:,}/{total:,} (100.00%).")
    print(
        f"Eligible configurations: {len(results):,} "
        f"(minimum resolved trades: {base.min_resolved_trades})."
    )
    print(f"Full ranked results saved to: {output_path.resolve()}")

    for rank, (hour, minute, tp, sl, result) in enumerate(
        results[:top_count], start=1
    ):
        print(
            f"\nRank {rank}: Time={hour:02d}:{minute:02d} "
            f"Direction={result.direction} TP={tp:g} SL={sl:g} "
            f"RR={tp / sl:.2f}"
        )
        print(format_result(result))

    if not results:
        print("No configurations met min_resolved_trades.")

    print(f"\nTotal runtime: {duration_text(time.perf_counter() - started)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
