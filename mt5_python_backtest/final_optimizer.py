from __future__ import annotations

import argparse
import csv
import json
import math
import os
import sys
import time
from dataclasses import asdict, is_dataclass
from datetime import datetime
from itertools import product
from pathlib import Path
from concurrent.futures import ProcessPoolExecutor, as_completed

from backtest import Config, load_bars, run_backtest


BASE_DIR = Path(__file__).resolve().parent
DEFAULT_DATA = BASE_DIR / "XAUUSD.sc_M1_202208290100_202609302357.csv"

CHECKPOINT_FILE = BASE_DIR / "final_optimizer_checkpoint.json"
PARTIAL_FILE = BASE_DIR / "final_optimizer_partial.csv"
FINAL_CSV = BASE_DIR / "final_optimization_results.csv"
FINAL_HTML = BASE_DIR / "final_optimization_results.html"
MONTHLY_CSV = BASE_DIR / "final_monthly_results.csv"
YEARLY_CSV = BASE_DIR / "final_yearly_results.csv"

MIN_RR_DEFAULT = 1.50
MAX_RR_DEFAULT = 2.00


def parse_range(text: str) -> list[int]:
    parts = text.split(":")
    if len(parts) != 3:
        raise ValueError(f"Invalid range: {text}. Use START:STOP:STEP")

    start, stop, step = map(int, parts)

    if step <= 0:
        raise ValueError("Step must be > 0")

    if stop < start:
        raise ValueError("STOP must be >= START")

    return list(range(start, stop + 1, step))


def rr(tp: float, sl: float) -> float:
    return tp / sl if sl else 0.0


def obj_value(obj, name, default=None):
    return getattr(obj, name, default)


def result_to_dict(result, hour, minute, direction, tp, sl):
    return {
        "hour": hour,
        "minute": minute,
        "time": f"{hour:02d}:{minute:02d}",
        "direction": direction,
        "tp": tp,
        "sl": sl,
        "rr": rr(tp, sl),

        "signals": int(obj_value(result, "signals",
                          obj_value(result, "total", 0)) or 0),
        "wins": int(obj_value(result, "wins", 0) or 0),
        "losses": int(obj_value(result, "losses", 0) or 0),
        "pending": int(obj_value(result, "pending", 0) or 0),

        "win_rate": float(obj_value(result, "win_rate", 0.0) or 0.0),
        "net_r": float(obj_value(result, "net_r", 0.0) or 0.0),
        "profit_factor": float(
            obj_value(result, "profit_factor", 0.0) or 0.0
        ),

        "max_sl_streak": int(
            obj_value(result, "max_consecutive_sl",
                      obj_value(result, "max_sl", 0)) or 0
        ),

        "max_sl_days": int(
            obj_value(result, "max_sl_days", 0) or 0
        ),

        "max_dd_r": float(
            obj_value(result, "max_dd_r", 0.0) or 0.0
        ),

        "max_dd_dollars": float(
            obj_value(result, "max_dd_dollars", 0.0) or 0.0
        ),

        "max_dd_percent": float(
            obj_value(result, "max_dd_percent", 0.0) or 0.0
        ),

        "worst_equity": float(
            obj_value(result, "worst_equity", 0.0) or 0.0
        ),
    }


def rank_key(r):
    # Prop-firm oriented ranking:
    # 1) Lowest Max DD %
    # 2) Lowest Max consecutive loss streak
    # 3) Highest Profit Factor
    # 4) Highest Net R
    # 5) Highest Win Rate
    return (
        r["max_dd_percent"],
        r["max_sl_streak"],
        -r["profit_factor"],
        -r["net_r"],
        -r["win_rate"],
    )


def calculate_correct_dd(trades, tp, sl, initial_balance=25000.0,
                         dd_risk_percent=0.7):
    """
    Independent Prop-Firm DD calculation.

    Fixed risk per resolved trade:
        risk_money = initial_balance * dd_risk_percent / 100

    WIN:
        + risk_money * (TP / SL)

    LOSS:
        - risk_money

    DD:
        peak equity -> current equity
    """

    risk_money = initial_balance * dd_risk_percent / 100.0

    if risk_money <= 0:
        return 0.0, 0.0, 0.0, initial_balance

    equity = initial_balance
    peak = initial_balance

    max_dd_dollars = 0.0
    worst_equity = initial_balance

    ordered = sorted(
        [
            t for t in trades
            if getattr(t, "outcome", None) in {"WIN", "LOSS"}
        ],
        key=lambda t: (
            getattr(t, "exit_time", None),
            getattr(t, "signal_time", None),
        ),
    )

    win_r = float(tp) / float(sl)

    for trade in ordered:
        outcome = getattr(trade, "outcome", None)

        if outcome == "WIN":
            equity += risk_money * win_r

        elif outcome == "LOSS":
            equity -= risk_money

        peak = max(peak, equity)
        worst_equity = min(worst_equity, equity)

        dd = peak - equity
        max_dd_dollars = max(max_dd_dollars, dd)

    max_dd_r = max_dd_dollars / risk_money
    max_dd_percent = max_dd_dollars / initial_balance * 100.0

    return (
        max_dd_r,
        max_dd_dollars,
        max_dd_percent,
        worst_equity,
    )


_WORKER_BARS = None
_WORKER_BASE = None


def worker_init(data_file: str):
    global _WORKER_BARS, _WORKER_BASE

    _WORKER_BARS = load_bars(Path(data_file))

    _WORKER_BASE = {
        "symbol": "XAUUSD.sc",
        "pip_size": 0.1,
        "same_bar_rule": 0,
        "max_hold_bars": 0,
        "initial_balance": 25000.0,
        "dd_risk_percent": 0.7,
        "min_resolved_trades": 30,
        "trading_days": (
            "Sunday",
            "Monday",
            "Tuesday",
            "Wednesday",
            "Thursday",
            "Friday",
            "Saturday",
        ),
    }


def worker_run(task):
    hour, minute, direction, tp, sl = task

    cfg = Config(
        symbol=_WORKER_BASE["symbol"],
        direction=direction,
        signal_hour=hour,
        signal_minute=minute,
        take_profit_pips=float(tp),
        stop_loss_pips=float(sl),
        pip_size=_WORKER_BASE["pip_size"],
        trading_days=_WORKER_BASE["trading_days"],
        same_bar_rule=_WORKER_BASE["same_bar_rule"],
        max_hold_bars=_WORKER_BASE["max_hold_bars"],
        initial_balance=_WORKER_BASE["initial_balance"],
        dd_risk_percent=_WORKER_BASE["dd_risk_percent"],
        min_resolved_trades=_WORKER_BASE["min_resolved_trades"],
    )

    result, trades = run_backtest(_WORKER_BARS, cfg)

    row = result_to_dict(
        result,
        hour,
        minute,
        direction,
        tp,
        sl,
    )

    # IMPORTANT:
    # Do not trust a previously modified DD calculation inside backtest.py.
    # Recalculate Prop-Firm DD independently here.
    (
        max_dd_r,
        max_dd_dollars,
        max_dd_percent,
        worst_equity,
    ) = calculate_correct_dd(
        trades,
        tp,
        sl,
        initial_balance=cfg.initial_balance,
        dd_risk_percent=cfg.dd_risk_percent,
    )

    row["max_dd_r"] = max_dd_r
    row["max_dd_dollars"] = max_dd_dollars
    row["max_dd_percent"] = max_dd_percent
    row["worst_equity"] = worst_equity

    return row


def save_checkpoint(completed, total, results):
    data = {
        "completed": completed,
        "total": total,
        "results": results,
        "updated": datetime.now().isoformat(),
    }

    tmp = CHECKPOINT_FILE.with_suffix(".tmp")

    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f)

    os.replace(tmp, CHECKPOINT_FILE)


def load_checkpoint():
    if not CHECKPOINT_FILE.exists():
        return 0, []

    try:
        with open(CHECKPOINT_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)

        return int(data.get("completed", 0)), data.get("results", [])

    except Exception:
        print("WARNING: checkpoint could not be read. Starting from zero.")
        return 0, []


def write_partial(results):
    if not results:
        return

    fields = list(results[0].keys())

    with open(PARTIAL_FILE, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fields)
        writer.writeheader()
        writer.writerows(results)


def format_time(seconds):
    seconds = int(max(0, seconds))
    h = seconds // 3600
    m = (seconds % 3600) // 60
    s = seconds % 60
    return f"{h:02d}:{m:02d}:{s:02d}"


def write_final_csv(results):
    if not results:
        return

    fields = list(results[0].keys())

    with open(FINAL_CSV, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fields)
        writer.writeheader()
        writer.writerows(results)


def safe(v):
    if isinstance(v, float):
        if math.isinf(v):
            return "INF"
        return f"{v:.4f}"
    return str(v)


def write_html(results, monthly=None, yearly=None):
    top = results[:200]

    html = [
        "<!doctype html>",
        "<html><head><meta charset='utf-8'>",
        "<title>Final Prop Firm Optimizer</title>",
        "<style>",
        "body{font-family:Arial;background:#111;color:#eee;margin:20px}",
        "h1,h2{margin-top:25px}",
        "table{border-collapse:collapse;width:100%;font-size:12px}",
        "th{background:#333;position:sticky;top:0;padding:7px}",
        "td{padding:6px;border:1px solid #444;text-align:center}",
        "tr:nth-child(even){background:#191919}",
        ".good{font-weight:bold}",
        "</style></head><body>",
        "<h1>Prop Firm Time / TP / SL Optimizer</h1>",
        "<p>Minimum RR = 1.50 | Maximum RR = 2.00</p>",
        "<table><thead><tr>",
    ]

    headers = [
        "Rank", "Time", "Direction", "TP", "SL", "RR",
        "Signals", "Wins", "Losses", "WR",
        "PF", "Net R", "Max SL", "Max SL Days",
        "Max DD R", "Max DD $", "Max DD %", "Worst Equity"
    ]

    for h in headers:
        html.append(f"<th>{h}</th>")

    html.append("</tr></thead><tbody>")

    for i, r in enumerate(top, 1):
        html.append("<tr>")
        values = [
            i,
            r["time"],
            r["direction"],
            r["tp"],
            r["sl"],
            f'{r["rr"]:.2f}',
            r["signals"],
            r["wins"],
            r["losses"],
            f'{r["win_rate"]:.2f}%',
            f'{r["profit_factor"]:.3f}',
            f'{r["net_r"]:.3f}',
            r["max_sl_streak"],
            r["max_sl_days"],
            f'{r["max_dd_r"]:.3f}',
            f'{r["max_dd_dollars"]:.2f}',
            f'{r["max_dd_percent"]:.3f}%',
            f'{r["worst_equity"]:.2f}',
        ]

        for v in values:
            html.append(f"<td>{v}</td>")

        html.append("</tr>")

    html.extend(["</tbody></table>"])

    # Prop-Firm summary for the #1 candidate
    if results:
        best = results[0]

        html.append("<h2>Prop-Firm Summary - #1 Candidate</h2>")
        html.append("<table>")
        html.append("<tr><th>Metric</th><th>Value</th></tr>")

        summary_items = [
            ("Time", best["time"]),
            ("Direction", best["direction"]),
            ("TP", best["tp"]),
            ("SL", best["sl"]),
            ("RR", f'{best["rr"]:.2f}'),
            ("Trades", best["signals"]),
            ("Win Rate", f'{best["win_rate"]:.2f}%'),
            ("Profit Factor", f'{best["profit_factor"]:.3f}'),
            ("Net R", f'{best["net_r"]:.3f}'),
            ("Max Loss Streak", best["max_sl_streak"]),
            ("Max DD R", f'{best["max_dd_r"]:.3f}'),
            ("Max DD $", f'{best["max_dd_dollars"]:.2f}'),
            ("Max DD %", f'{best["max_dd_percent"]:.3f}%'),
            ("Worst Equity", f'{best["worst_equity"]:.2f}'),
        ]

        for metric, value in summary_items:
            html.append(
                f"<tr><td>{metric}</td><td>{value}</td></tr>"
            )

        html.append("</table>")

    if monthly:
        html.append("<h2>Monthly Results - Best Candidate</h2>")
        html.append("<table><tr>")

        for h in monthly[0].keys():
            html.append(f"<th>{h}</th>")

        html.append("</tr>")

        for row in monthly:
            html.append("<tr>")
            for v in row.values():
                html.append(f"<td>{v}</td>")
            html.append("</tr>")

        html.append("</table>")

    if yearly:
        html.append("<h2>Yearly Results - Best Candidate</h2>")
        html.append("<table><tr>")

        for h in yearly[0].keys():
            html.append(f"<th>{h}</th>")

        html.append("</tr>")

        for row in yearly:
            html.append("<tr>")
            for v in row.values():
                html.append(f"<td>{v}</td>")
            html.append("</tr>")

        html.append("</table>")

    html.extend(["</body></html>"])

    with open(FINAL_HTML, "w", encoding="utf-8") as f:
        f.write("\n".join(html))


def extract_trade_value(trade, name, default=None):
    return getattr(trade, name, default)


def calculate_period_results(trades):
    monthly = {}
    yearly = {}

    for trade in trades:
        outcome = extract_trade_value(trade, "outcome")
        exit_time = extract_trade_value(trade, "exit_time")

        if outcome not in {"WIN", "LOSS"}:
            continue

        if exit_time is None:
            continue

        month_key = exit_time.strftime("%Y-%m")
        year_key = exit_time.strftime("%Y")

        for container, key in (
            (monthly, month_key),
            (yearly, year_key),
        ):
            if key not in container:
                container[key] = {
                    "period": key,
                    "trades": 0,
                    "wins": 0,
                    "losses": 0,
                    "net_r": 0.0,
                }

            x = container[key]
            x["trades"] += 1

            if outcome == "WIN":
                x["wins"] += 1
            else:
                x["losses"] += 1

    # Calculate Net R using the actual candidate TP/SL later.
    return monthly, yearly


def finalize_period_stats(raw, tp, sl):
    rows = []

    win_r = tp / sl

    for key in sorted(raw):
        x = raw[key]

        trades = x["trades"]
        wins = x["wins"]
        losses = x["losses"]

        net_r = wins * win_r - losses

        wr = wins / trades * 100 if trades else 0.0

        rows.append({
            "period": x["period"],
            "trades": trades,
            "wins": wins,
            "losses": losses,
            "win_rate": round(wr, 2),
            "net_r": round(net_r, 4),
        })

    return rows


def period_summary(monthly_rows):
    if not monthly_rows:
        return {}

    positive = sum(1 for x in monthly_rows if x["net_r"] > 0)
    negative = sum(1 for x in monthly_rows if x["net_r"] < 0)

    worst = min(monthly_rows, key=lambda x: x["net_r"])
    best = max(monthly_rows, key=lambda x: x["net_r"])

    max_loss_streak = 0
    current = 0

    for x in monthly_rows:
        if x["net_r"] < 0:
            current += 1
            max_loss_streak = max(max_loss_streak, current)
        else:
            current = 0

    avg_trades = sum(x["trades"] for x in monthly_rows) / len(monthly_rows)

    return {
        "months": len(monthly_rows),
        "profitable_months": positive,
        "losing_months": negative,
        "best_month": best["period"],
        "best_month_r": best["net_r"],
        "worst_month": worst["period"],
        "worst_month_r": worst["net_r"],
        "max_losing_month_streak": max_loss_streak,
        "avg_trades_month": round(avg_trades, 2),
        "min_trades_month": min(x["trades"] for x in monthly_rows),
        "max_trades_month": max(x["trades"] for x in monthly_rows),
    }


def analyze_best_candidate(best_row, data_file):
    print()
    print("=" * 70)
    print("DETAILED MONTHLY / YEARLY ANALYSIS")
    print("=" * 70)

    print(
        f'Candidate: {best_row["time"]} {best_row["direction"]} '
        f'TP={best_row["tp"]} SL={best_row["sl"]} '
        f'RR={best_row["rr"]:.2f}'
    )

    bars = load_bars(Path(data_file))

    cfg = Config(
        symbol="XAUUSD.sc",
        direction=best_row["direction"],
        signal_hour=best_row["hour"],
        signal_minute=best_row["minute"],
        take_profit_pips=float(best_row["tp"]),
        stop_loss_pips=float(best_row["sl"]),
        pip_size=0.1,
        trading_days=(
            "Sunday",
            "Monday",
            "Tuesday",
            "Wednesday",
            "Thursday",
            "Friday",
            "Saturday",
        ),
        same_bar_rule=0,
        max_hold_bars=0,
        initial_balance=25000.0,
        dd_risk_percent=0.7,
        min_resolved_trades=30,
    )

    _result, trades = run_backtest(bars, cfg)

    raw_monthly, raw_yearly = calculate_period_results(trades)

    monthly = finalize_period_stats(
        raw_monthly,
        best_row["tp"],
        best_row["sl"],
    )

    yearly = finalize_period_stats(
        raw_yearly,
        best_row["tp"],
        best_row["sl"],
    )

    summary = period_summary(monthly)

    print()
    print("MONTH SUMMARY")
    print(f'Months: {summary.get("months", 0)}')
    print(f'Profitable: {summary.get("profitable_months", 0)}')
    print(f'Losing: {summary.get("losing_months", 0)}')
    print(f'Worst month: {summary.get("worst_month")} ({summary.get("worst_month_r")}R)')
    print(f'Best month: {summary.get("best_month")} ({summary.get("best_month_r")}R)')
    print(f'Max losing-month streak: {summary.get("max_losing_month_streak")}')
    print(f'Average trades/month: {summary.get("avg_trades_month")}')
    print(f'Min trades/month: {summary.get("min_trades_month")}')
    print(f'Max trades/month: {summary.get("max_trades_month")}')

    with open(MONTHLY_CSV, "w", newline="", encoding="utf-8") as f:
        if monthly:
            writer = csv.DictWriter(f, fieldnames=monthly[0].keys())
            writer.writeheader()
            writer.writerows(monthly)

    with open(YEARLY_CSV, "w", newline="", encoding="utf-8") as f:
        if yearly:
            writer = csv.DictWriter(f, fieldnames=yearly[0].keys())
            writer.writeheader()
            writer.writerows(yearly)

    return monthly, yearly


def build_tasks(hours, minutes, tps, sls, directions, min_rr, max_rr):
    tasks = []

    for hour, minute, direction, tp, sl in product(
        hours, minutes, directions, tps, sls
    ):
        current_rr = rr(tp, sl)

        if current_rr < min_rr:
            continue

        if current_rr > max_rr:
            continue

        tasks.append((hour, minute, direction, tp, sl))

    return tasks


def main():
    parser = argparse.ArgumentParser(
        description="Final Prop Firm Time/TP/SL optimizer"
    )

    parser.add_argument(
        "--data",
        default=str(DEFAULT_DATA),
    )

    parser.add_argument(
        "--hours",
        default="0:23:1",
    )

    parser.add_argument(
        "--minutes",
        default="0:59:1",
    )

    parser.add_argument(
        "--tp",
        default="50:100:5",
    )

    parser.add_argument(
        "--sl",
        default="30:100:5",
    )

    parser.add_argument(
        "--directions",
        default="BUY",
    )

    parser.add_argument(
        "--min-rr",
        type=float,
        default=MIN_RR_DEFAULT,
    )

    parser.add_argument(
        "--max-rr",
        type=float,
        default=MAX_RR_DEFAULT,
    )

    parser.add_argument(
        "--workers",
        type=int,
        default=4,
    )

    parser.add_argument(
        "--top",
        type=int,
        default=200,
    )

    parser.add_argument(
        "--checkpoint-every",
        type=int,
        default=250,
    )

    parser.add_argument(
        "--fresh",
        action="store_true",
        help="Delete old checkpoint and start from zero",
    )

    args = parser.parse_args()

    if args.fresh:
        for path in (
            CHECKPOINT_FILE,
            PARTIAL_FILE,
        ):
            if path.exists():
                path.unlink()
        print("Old checkpoint/partial result removed.")

    hours = parse_range(args.hours)
    minutes = parse_range(args.minutes)
    tps = parse_range(args.tp)
    sls = parse_range(args.sl)

    directions = [
        x.strip().upper()
        for x in args.directions.split(",")
        if x.strip()
    ]

    tasks = build_tasks(
        hours,
        minutes,
        tps,
        sls,
        directions,
        args.min_rr,
        args.max_rr,
    )

    total = len(tasks)

    if total == 0:
        print("No valid configurations.")
        return 1

    completed, results = load_checkpoint()

    if completed > total:
        completed = 0
        results = []

    if completed:
        print("=" * 70)
        print("RESUMING PREVIOUS OPTIMIZATION")
        print("=" * 70)
        print(f"Checkpoint: {completed:,}/{total:,}")
        print(f"Remaining:  {total-completed:,}")
    else:
        print("=" * 70)
        print("NEW OPTIMIZATION")
        print("=" * 70)

    print(f"Data: {args.data}")
    print(f"Workers: {args.workers}")
    print(f"Hours: {args.hours}")
    print(f"Minutes: {args.minutes}")
    print(f"TP: {args.tp}")
    print(f"SL: {args.sl}")
    print(f"Directions: {directions}")
    print(f"Minimum RR: {args.min_rr:.2f}")
    print(f"Maximum RR: {args.max_rr:.2f}")
    print(f"Valid configurations: {total:,}")
    print()

    remaining_tasks = tasks[completed:]

    start_time = time.time()
    last_checkpoint = completed

    with ProcessPoolExecutor(
        max_workers=args.workers,
        initializer=worker_init,
        initargs=(args.data,),
    ) as executor:

        futures = {
            executor.submit(worker_run, task): task
            for task in remaining_tasks
        }

        done = completed

        for future in as_completed(futures):
            task = futures[future]

            try:
                row = future.result()

                if row["signals"] >= 30:
                    results.append(row)

            except Exception as exc:
                print()
                print("ERROR:", task, exc)

            done += 1

            elapsed = time.time() - start_time
            new_count = done - completed

            if new_count > 0:
                rate = new_count / elapsed
                remaining = total - done
                eta = remaining / rate if rate > 0 else 0

                percent = done / total * 100

                print(
                    f"\rProgress {done:,}/{total:,} "
                    f"({percent:6.2f}%) | "
                    f"Elapsed {format_time(elapsed)} | "
                    f"ETA {format_time(eta)}",
                    end="",
                    flush=True,
                )

            if (
                done - last_checkpoint >= args.checkpoint_every
                or done == total
            ):
                results.sort(key=rank_key)

                save_checkpoint(done, total, results)
                write_partial(results)

                last_checkpoint = done

                print()
                print(
                    f"Checkpoint saved: "
                    f"{done:,}/{total:,} | "
                    f"Eligible: {len(results):,}"
                )

    print()
    print()
    print("=" * 70)
    print("OPTIMIZATION COMPLETE")
    print("=" * 70)

    results.sort(key=rank_key)

    results = results[:args.top]

    write_final_csv(results)

    if not results:
        print("No eligible configurations.")
        return 0

    print(f"Eligible configurations retained: {len(results):,}")
    print(f"CSV:  {FINAL_CSV}")

    print()
    print("TOP RESULTS")
    print("-" * 110)

    for i, r in enumerate(results[:20], 1):
        print(
            f'{i:>3}. {r["time"]} {r["direction"]} '
            f'TP={r["tp"]:<3} SL={r["sl"]:<3} '
            f'RR={r["rr"]:.2f} | '
            f'Trades={r["signals"]:<4} '
            f'WR={r["win_rate"]:.2f}% | '
            f'PF={r["profit_factor"]:.3f} | '
            f'NetR={r["net_r"]:.2f} | '
            f'MaxSL={r["max_sl_streak"]} | '
            f'DD={r["max_dd_percent"]:.2f}%'
        )

    best = results[0]

    # Safety validation
    if best["worst_equity"] > 25000.0001:
        print()
        print("WARNING: Worst Equity is above initial balance.")
        print("DD result should be reviewed before trusting this run.")

    if best["max_dd_percent"] < 0 or best["max_dd_dollars"] < 0:
        print()
        print("WARNING: Invalid negative DD detected.")

    print()
    print(
        f'Best candidate DD validation: '
        f'MaxDD={best["max_dd_percent"]:.3f}% | '
        f'WorstEquity={best["worst_equity"]:.2f}'
    )

    monthly, yearly = analyze_best_candidate(
        best,
        args.data,
    )

    write_html(
        results,
        monthly=monthly,
        yearly=yearly,
    )

    print()
    print("=" * 70)
    print("FILES")
    print("=" * 70)
    print(f"Final CSV:     {FINAL_CSV}")
    print(f"Final HTML:    {FINAL_HTML}")
    print(f"Monthly CSV:   {MONTHLY_CSV}")
    print(f"Yearly CSV:    {YEARLY_CSV}")
    print(f"Checkpoint:    {CHECKPOINT_FILE}")
    print(f"Partial CSV:   {PARTIAL_FILE}")
    print()
    print("Optimization finished successfully.")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
