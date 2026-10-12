#!/usr/bin/env python3
"""Parallel grid optimizer with TP > SL filtering and live progress."""

from __future__ import annotations

import argparse
import csv
import html
import itertools
import json
import math
import os
import time
from concurrent.futures import ProcessPoolExecutor
from dataclasses import replace
from pathlib import Path
from typing import Any, Iterable, Iterator

from backtest import BacktestResult, Config, format_result, load_bars, run_backtest

_WORKER_BARS = None
_WORKER_BASE = None


def range_values(spec: list[int | float]) -> list[int | float]:
    if len(spec) != 3:
        raise ValueError("Range must be [start, end, step]")
    start, end, step = spec
    if step <= 0 or end < start:
        raise ValueError("Invalid range")
    values = []
    current = start
    while current <= end:
        values.append(current)
        current += step
    return values


def parse_range(value: str) -> list[int]:
    try:
        parts = [int(p) for p in value.split(":")]
    except ValueError as exc:
        raise argparse.ArgumentTypeError(
            "Use START:END[:STEP], e.g. 50:100:5"
        ) from exc
    if len(parts) == 2:
        parts.append(1)
    if len(parts) != 3 or parts[2] <= 0 or parts[1] < parts[0]:
        raise argparse.ArgumentTypeError("Invalid ascending range")
    return list(range(parts[0], parts[1] + 1, parts[2]))


def pf_rank(result: BacktestResult) -> float:
    return -1.0 if result.profit_factor is None else result.profit_factor


def rank_key(result: BacktestResult):
    # Existing ranking: lowest SL streak, then WR, Net R, PF.
    return (
        result.max_consecutive_sl,
        -result.win_rate,
        -result.net_r,
        -pf_rank(result),
    )


def duration_text(seconds: float | None) -> str:
    if seconds is None or not math.isfinite(seconds):
        return "--"
    seconds = max(0, int(seconds))
    h, rem = divmod(seconds, 3600)
    m, s = divmod(rem, 60)
    return f"{h:02d}:{m:02d}:{s:02d}" if h else f"{m:02d}:{s:02d}"


def show_progress(done: int, total: int, started: float, workers: int) -> None:
    elapsed = time.perf_counter() - started
    fraction = done / total if total else 1.0
    filled = int(30 * fraction)
    bar = "#" * filled + "-" * (30 - filled)
    eta = elapsed * (total - done) / done if done else None
    print(
        f"\rProgress: [{bar}] {fraction * 100:6.2f}% | "
        f"{done:,}/{total:,} | Workers={workers} | "
        f"Elapsed {duration_text(elapsed)} | ETA {duration_text(eta)}",
        end="", flush=True,
    )


def init_worker(csv_path: str, settings: dict[str, Any]) -> None:
    global _WORKER_BARS, _WORKER_BASE
    # Each process loads the CSV once, not once per configuration.
    _WORKER_BARS = load_bars(csv_path)
    _WORKER_BASE = Config.from_dict(settings)


def run_chunk(chunk):
    if _WORKER_BARS is None or _WORKER_BASE is None:
        raise RuntimeError("Worker initialization failed")

    eligible = []
    for hour, minute, tp, sl, direction in chunk:
        cfg = replace(
            _WORKER_BASE,
            signal_hour=hour,
            signal_minute=minute,
            take_profit_pips=tp,
            stop_loss_pips=sl,
            direction=direction,
        )
        result, _ = run_backtest(_WORKER_BARS, cfg)
        if result.resolved >= cfg.min_resolved_trades:
            eligible.append((hour, minute, tp, sl, result))
    return len(chunk), eligible


def chunked(items: Iterable, size: int) -> Iterator[list]:
    chunk = []
    for item in items:
        chunk.append(item)
        if len(chunk) >= size:
            yield chunk
            chunk = []
    if chunk:
        yield chunk


def save_results(path: Path, results) -> None:
    columns = [
        "Rank", "SignalTime", "Direction", "TP", "SL", "RR",
        "Signals", "Wins", "Losses", "Pending", "Resolved",
        "WinRate", "NetR", "ProfitFactor", "MaxConsecutiveSL",
        "MaxSLCalendarDays", "MaxDD_R", "MaxDD_Dollars",
        "MaxDD_Percent", "WorstEquity",
    ]
    with path.open("w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=columns)
        writer.writeheader()
        for rank, (hour, minute, tp, sl, result) in enumerate(results, 1):
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



def save_html_results(path: Path, results) -> None:
    """Save the complete ranked optimization results as HTML."""
    headers = [
        "Rank", "Time", "Direction", "TP", "SL", "RR",
        "Signals", "Wins", "Losses", "Pending", "Win Rate",
        "Net R", "Profit Factor", "Max SL Streak",
        "Max SL Days", "Max DD R", "Max DD $",
        "Max DD %", "Worst Equity",
    ]

    with path.open("w", encoding="utf-8") as f:
        f.write("""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>TP/SL Optimization Results</title>
<style>
body{font-family:Arial,sans-serif;background:#111;color:#eee;margin:20px}
h2{margin-bottom:8px}.note{color:#bbb;font-size:13px;margin-bottom:15px}
.wrap{overflow:auto;max-height:85vh;border:1px solid #444}
table{border-collapse:collapse;width:100%;font-size:12px;white-space:nowrap}
th{position:sticky;top:0;background:#303030}
th,td{padding:8px;border:1px solid #444;text-align:right}
tr:nth-child(even){background:#1b1b1b}
</style></head><body>
<h2>Python TP / SL Optimization Results</h2>
""")
        f.write(
            f'<div class="note">Eligible results: {len(results):,}. '
            'Filter: TP &gt; SL. Ranking: lowest SL streak, then highest '
            'Win Rate, Net R and Profit Factor.</div>'
        )
        f.write('<div class="wrap"><table><thead><tr>')
        for header in headers:
            f.write(f"<th>{html.escape(header)}</th>")
        f.write("</tr></thead><tbody>")

        for rank, (hour, minute, tp, sl, r) in enumerate(results, 1):
            values = [
                rank, f"{hour:02d}:{minute:02d}", r.direction,
                f"{tp:g}", f"{sl:g}", f"{tp/sl:.2f}",
                r.signals, r.wins, r.losses, r.pending,
                f"{r.win_rate:.2f}%", f"{r.net_r:.4f}R",
                "INF" if r.profit_factor is None else f"{r.profit_factor:.4f}",
                r.max_consecutive_sl, r.max_sl_calendar_days,
                f"{r.max_dd_r:.4f}R", f"${r.max_dd_dollars:,.2f}",
                f"{r.max_dd_percent:.4f}%", f"${r.worst_equity:,.2f}",
            ]
            f.write("<tr>")
            for value in values:
                f.write(f"<td>{html.escape(str(value))}</td>")
            f.write("</tr>")
        f.write("</tbody></table></div></body></html>")



def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", default="config.json")
    parser.add_argument("--csv", dest="csv_path")
    parser.add_argument("--hours", type=parse_range)
    parser.add_argument("--minutes", type=parse_range)
    parser.add_argument("--tp", type=parse_range)
    parser.add_argument("--sl", type=parse_range)
    parser.add_argument("--directions")
    parser.add_argument("--top", type=int)
    parser.add_argument(
        "--workers", type=int,
        default=min(4, os.cpu_count() or 1),
        help="Parallel worker processes (default: up to 4)",
    )
    parser.add_argument("--chunk-size", type=int, default=20)
    parser.add_argument("--output", default="optimization_results.csv")
    parser.add_argument("--html-output", default=None, help="HTML output path")
    args = parser.parse_args()

    if args.workers < 1 or args.chunk_size < 1:
        parser.error("--workers and --chunk-size must be positive")

    with Path(args.config).open(encoding="utf-8") as f:
        settings = json.load(f)

    base = Config.from_dict(settings["backtest"])
    opt = settings["optimization"]

    hours = args.hours or [int(v) for v in range_values(opt["signal_hours"])]
    minutes = args.minutes or [int(v) for v in range_values(opt["signal_minutes"])]
    tps = [float(v) for v in (args.tp or range_values(opt["take_profit_pips"]))]
    sls = [float(v) for v in (args.sl or range_values(opt["stop_loss_pips"]))]
    directions = (
        [v.strip().upper() for v in args.directions.split(",")]
        if args.directions
        else [v.upper() for v in opt["directions"]]
    )
    top_count = args.top if args.top is not None else int(opt.get("top", 20))

    if top_count < 1:
        parser.error("--top must be positive")
    if not directions or any(d not in {"BUY", "SELL"} for d in directions):
        parser.error("Directions must contain BUY and/or SELL")
    if not hours or not minutes or not tps or not sls:
        parser.error("Ranges must not be empty")

    # Keep only combinations with TP strictly greater than SL.
    valid_pairs = [(tp, sl) for tp, sl in itertools.product(tps, sls) if tp > sl]
    if not valid_pairs:
        parser.error("No valid combinations: TP must be greater than SL")

    total = len(hours) * len(minutes) * len(valid_pairs) * len(directions)
    csv_path = str(Path(args.csv_path or settings["data_file"]).resolve())
    workers = min(args.workers, total)
    output_path = Path(args.output)
    html_path = (
        Path(args.html_output) if args.html_output
        else output_path.with_suffix(".html")
    )

    # Partial results from a previous run are intentionally discarded.
    if output_path.exists():
        output_path.unlink()
        print(f"Removed previous result file: {output_path}")

    print(f"Data file: {csv_path}")
    print(f"Available CPU cores: {os.cpu_count() or 'unknown'}")
    print(f"Workers: {workers}")
    print(f"Hours: {len(hours)} | Minutes: {len(minutes)}")
    print(f"Valid TP/SL pairs (TP > SL): {len(valid_pairs)}")
    print(f"Directions: {', '.join(directions)}")
    print(f"Total configurations: {total:,}")
    print("Starting parallel workers; each will load M1 data once.")

    candidates = (
        (int(h), int(m), float(tp), float(sl), direction)
        for h, m, (tp, sl), direction in itertools.product(
            hours, minutes, valid_pairs, directions
        )
    )
    tasks = chunked(candidates, args.chunk_size)
    results = []
    completed = 0
    started = time.perf_counter()
    last_update = 0.0

    print("Parallel optimization started. Partial results are not saved.")
    show_progress(0, total, started, workers)

    try:
        with ProcessPoolExecutor(
            max_workers=workers,
            initializer=init_worker,
            initargs=(csv_path, settings["backtest"]),
        ) as pool:
            for chunk_done, chunk_results in pool.map(
                run_chunk, tasks, chunksize=1
            ):
                completed += chunk_done
                results.extend(chunk_results)
                now = time.perf_counter()
                if now - last_update >= 0.5 or completed == total:
                    show_progress(completed, total, started, workers)
                    last_update = now
    except KeyboardInterrupt:
        print("\nOptimization interrupted. No partial results saved.")
        return 130

    print()
    results.sort(key=lambda row: rank_key(row[4]))
    save_results(output_path, results)
    html_path = output_path.with_suffix(".html")
    save_html_results(html_path, results)

    print(f"Completed: {completed:,}/{total:,} (100.00%).")
    print(f"Eligible configurations: {len(results):,}.")
    print(f"Full ranked CSV saved to: {output_path.resolve()}")
    print(f"Full ranked HTML saved to: {html_path.resolve()}")

    for rank, (hour, minute, tp, sl, result) in enumerate(results[:top_count], 1):
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
