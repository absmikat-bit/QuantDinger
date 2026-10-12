from __future__ import annotations

import csv
from dataclasses import dataclass
from datetime import datetime, timedelta
from collections import defaultdict


# ============================================================
# CONFIG
# ============================================================

DATA_FILE = "FTMO XAUUSD_M1_202208290105_202609302349.csv"

SIGNAL_HOUR = 9
SIGNAL_MINUTE = 34

# TP/SL: 50 -> 100, step 1
TP_START = 50
TP_END = 100
TP_STEP = 1

SL_START = 50
SL_END = 100
SL_STEP = 1

PIP_SIZE = 0.1

INITIAL_BALANCE = 25000.0
RISK_PERCENT = 0.7
RISK_MONEY = INITIAL_BALANCE * RISK_PERCENT / 100.0

# 0 = SL first if both TP/SL touched in same M1 bar
SAME_BAR_RULE = 0

# Friday FTMO session close approximation
FRIDAY_CLOSE_HOUR = 23
FRIDAY_CLOSE_MINUTE = 49

OUTPUT_FILE = "ftmo_0934_optimization_results.csv"
TOP_FILE = "ftmo_0934_top_results.csv"


# ============================================================
# BAR
# ============================================================

@dataclass
class Bar:
    dt: datetime
    open: float
    high: float
    low: float
    close: float
    spread: float


# ============================================================
# TRADE
# ============================================================

@dataclass
class Trade:
    signal_dt: datetime
    entry_dt: datetime
    exit_dt: datetime

    entry: float
    exit_price: float

    tp: float
    sl: float

    r: float
    exit_type: str

    holding_days: float


# ============================================================
# LOAD DATA
# ============================================================

def load_bars(filename: str):
    bars = []

    print("Loading FTMO M1 data...")

    with open(filename, "r", encoding="utf-8", errors="ignore") as f:
        for line in f:
            line = line.strip()

            if not line:
                continue

            if line.startswith("<DATE>"):
                continue

            parts = line.split()

            if len(parts) < 9:
                continue

            try:
                dt = datetime.strptime(
                    parts[0] + " " + parts[1],
                    "%Y.%m.%d %H:%M:%S"
                )

                bars.append(
                    Bar(
                        dt=dt,
                        open=float(parts[2]),
                        high=float(parts[3]),
                        low=float(parts[4]),
                        close=float(parts[5]),
                        spread=float(parts[8]),
                    )
                )

            except (ValueError, IndexError):
                continue

    print(f"Loaded bars: {len(bars):,}")

    if bars:
        print(f"First bar: {bars[0].dt}")
        print(f"Last bar:  {bars[-1].dt}")

    return bars


# ============================================================
# DAY INDEX
# ============================================================

def build_day_index(bars):

    day_index = defaultdict(dict)

    for i, bar in enumerate(bars):
        key = bar.dt.date()
        minute = bar.dt.hour * 60 + bar.dt.minute

        day_index[key][minute] = i

    return day_index


# ============================================================
# GAP-AWARE EXIT
# ============================================================

def check_exit(bar, tp_price, sl_price):

    hit_tp = bar.high >= tp_price
    hit_sl = bar.low <= sl_price

    if hit_tp and hit_sl:

        if SAME_BAR_RULE == 0:
            return "SL", sl_price

        return "TP", tp_price

    if hit_sl:
        return "SL", sl_price

    if hit_tp:
        return "TP", tp_price

    return None, None


# ============================================================
# BACKTEST ONE CONFIG
# ============================================================

def run_config(
    bars,
    day_index,
    tp_pips,
    sl_pips,
    friday_close=False
):

    tp_distance = tp_pips * PIP_SIZE
    sl_distance = sl_pips * PIP_SIZE

    trades = []

    signal_count = 0
    wins = 0
    losses = 0
    friday_closes = 0

    max_open_positions = 0

    active = []

    # Process every calendar day where 09:34 exists
    dates = sorted(day_index.keys())

    for date in dates:

        minute_key = SIGNAL_HOUR * 60 + SIGNAL_MINUTE

        if minute_key not in day_index[date]:
            continue

        signal_idx = day_index[date][minute_key]

        signal_bar = bars[signal_idx]

        signal_count += 1

        # ----------------------------------------------------
        # BUY ENTRY
        #
        # FTMO CSV XAUUSD is 2-digit.
        # spread is points, therefore spread * 0.01
        # ----------------------------------------------------

        entry_price = signal_bar.close + signal_bar.spread * 0.01

        tp_price = entry_price + tp_distance
        sl_price = entry_price - sl_distance

        entry_dt = signal_bar.dt

        # ----------------------------------------------------
        # Search forward until TP / SL / Friday close
        # ----------------------------------------------------

        exit_found = False

        for j in range(signal_idx + 1, len(bars)):

            bar = bars[j]

            # Friday forced close at 23:49
            if (
                friday_close
                and entry_dt.weekday() == 4
                and bar.dt.date() == entry_dt.date()
                and bar.dt.hour == FRIDAY_CLOSE_HOUR
                and bar.dt.minute == FRIDAY_CLOSE_MINUTE
            ):

                # First check whether TP/SL was reached
                exit_type, exit_price = check_exit(
                    bar,
                    tp_price,
                    sl_price
                )

                if exit_type is not None:

                    if exit_type == "TP":
                        r = tp_pips / sl_pips
                        wins += 1
                    else:
                        r = -1.0
                        losses += 1

                    exit_dt = bar.dt

                    holding_days = (
                        exit_dt - entry_dt
                    ).total_seconds() / 86400.0

                    trades.append(
                        Trade(
                            signal_dt=signal_bar.dt,
                            entry_dt=entry_dt,
                            exit_dt=exit_dt,
                            entry=entry_price,
                            exit_price=exit_price,
                            tp=tp_price,
                            sl=sl_price,
                            r=r,
                            exit_type=exit_type,
                            holding_days=holding_days,
                        )
                    )

                    exit_found = True
                    break

                # No TP/SL -> forced close
                exit_price = bar.close

                price_move = exit_price - entry_price

                r = price_move / sl_distance

                if r > 0:
                    wins += 1
                else:
                    losses += 1

                friday_closes += 1

                exit_dt = bar.dt

                holding_days = (
                    exit_dt - entry_dt
                ).total_seconds() / 86400.0

                trades.append(
                    Trade(
                        signal_dt=signal_bar.dt,
                        entry_dt=entry_dt,
                        exit_dt=exit_dt,
                        entry=entry_price,
                        exit_price=exit_price,
                        tp=tp_price,
                        sl=sl_price,
                        r=r,
                        exit_type="FRIDAY_CLOSE",
                        holding_days=holding_days,
                    )
                )

                exit_found = True
                break

            # Don't process bars before entry
            if bar.dt <= entry_dt:
                continue

            exit_type, exit_price = check_exit(
                bar,
                tp_price,
                sl_price
            )

            if exit_type is None:
                continue

            if exit_type == "TP":
                r = tp_pips / sl_pips
                wins += 1

            else:
                r = -1.0
                losses += 1

            exit_dt = bar.dt

            holding_days = (
                exit_dt - entry_dt
            ).total_seconds() / 86400.0

            trades.append(
                Trade(
                    signal_dt=signal_bar.dt,
                    entry_dt=entry_dt,
                    exit_dt=exit_dt,
                    entry=entry_price,
                    exit_price=exit_price,
                    tp=tp_price,
                    sl=sl_price,
                    r=r,
                    exit_type=exit_type,
                    holding_days=holding_days,
                )
            )

            exit_found = True
            break

        # ----------------------------------------------------
        # No exit found
        # ----------------------------------------------------

        if not exit_found:
            pass

    # ========================================================
    # METRICS
    # ========================================================

    resolved = [
        t for t in trades
        if t.exit_type in ("TP", "SL", "FRIDAY_CLOSE")
    ]

    if not resolved:
        return None

    # --------------------------------------------------------
    # Win rate
    # --------------------------------------------------------

    resolved_count = len(resolved)

    wr = (
        wins / (wins + losses) * 100
        if (wins + losses) > 0
        else 0
    )

    # --------------------------------------------------------
    # Profit Factor
    # --------------------------------------------------------

    gross_profit = sum(
        t.r for t in resolved
        if t.r > 0
    )

    gross_loss = abs(
        sum(
            t.r for t in resolved
            if t.r < 0
        )
    )

    if gross_loss > 0:
        pf = gross_profit / gross_loss
    else:
        pf = float("inf")

    # --------------------------------------------------------
    # Net R
    # --------------------------------------------------------

    net_r = sum(t.r for t in resolved)

    # --------------------------------------------------------
    # Closed equity drawdown
    # --------------------------------------------------------

    equity = INITIAL_BALANCE
    peak = equity
    max_dd = 0.0

    max_sl_streak = 0
    current_sl_streak = 0

    for t in sorted(
        resolved,
        key=lambda x: x.exit_dt
    ):

        equity += t.r * RISK_MONEY

        if equity > peak:
            peak = equity

        dd = peak - equity

        if dd > max_dd:
            max_dd = dd

        if t.r < 0:
            current_sl_streak += 1

            if current_sl_streak > max_sl_streak:
                max_sl_streak = current_sl_streak

        else:
            current_sl_streak = 0

    dd_percent = (
        max_dd / INITIAL_BALANCE * 100
    )

    # --------------------------------------------------------
    # Holding time
    # --------------------------------------------------------

    avg_days = (
        sum(t.holding_days for t in resolved)
        / len(resolved)
    )

    max_days = max(
        t.holding_days
        for t in resolved
    )

    # --------------------------------------------------------
    # Max simultaneous positions
    # --------------------------------------------------------

    events = []

    for t in resolved:

        events.append(
            (t.entry_dt, 1)
        )

        events.append(
            (t.exit_dt, -1)
        )

    events.sort(
        key=lambda x: (
            x[0],
            x[1]
        )
    )

    open_count = 0

    for _, change in events:

        open_count += change

        if open_count > max_open_positions:
            max_open_positions = open_count

    return {
        "TP": tp_pips,
        "SL": sl_pips,
        "RR": tp_pips / sl_pips,

        "Signals": signal_count,
        "Resolved": resolved_count,

        "Wins": wins,
        "Losses": losses,
        "FridayClose": friday_closes,

        "WR_%": wr,
        "PF": pf,
        "NetR": net_r,

        "DD_%": dd_percent,

        "MaxSL": max_sl_streak,

        "AvgDays": avg_days,
        "MaxDays": max_days,

        "MaxOpen": max_open_positions,
    }


# ============================================================
# MAIN
# ============================================================

def main():

    print("=" * 70)
    print("FTMO XAUUSD 09:34 FIXED OPTIMIZER")
    print("=" * 70)

    print()
    print("Signal Time : 09:34 BUY")
    print("TP Range    : 50 - 100, step 1")
    print("SL Range    : 50 - 100, step 1")
    print("Risk        : 0.7%")
    print("Balance     : $25,000")
    print()

    bars = load_bars(DATA_FILE)

    if not bars:
        print("ERROR: No bars loaded.")
        return

    print()
    print("Building day index...")

    day_index = build_day_index(bars)

    total_configs = (
        ((TP_END - TP_START) // TP_STEP) + 1
    ) * (
        ((SL_END - SL_START) // SL_STEP) + 1
    )

    print(f"Total configurations per mode: {total_configs:,}")
    print(f"Total configurations: {total_configs * 2:,}")
    print()

    all_results = []

    modes = [
        ("FRIDAY_CLOSE", True),
        ("NO_FORCE_CLOSE", False),
    ]

    completed = 0

    # ========================================================
    # RUN ALL CONFIGURATIONS
    # ========================================================

    for mode_name, force_close in modes:

        print()
        print("=" * 70)
        print(mode_name)
        print("=" * 70)

        for tp in range(
            TP_START,
            TP_END + 1,
            TP_STEP
        ):

            for sl in range(
                SL_START,
                SL_END + 1,
                SL_STEP
            ):

                result = run_config(
                    bars,
                    day_index,
                    tp,
                    sl,
                    friday_close=force_close
                )

                completed += 1

                if result is None:
                    continue

                result["Mode"] = mode_name

                all_results.append(result)

                if completed % 200 == 0:
                    print(
                        f"Progress: "
                        f"{completed:,}/"
                        f"{total_configs * 2:,}"
                    )

    print()
    print("=" * 70)
    print("OPTIMIZATION COMPLETE")
    print("=" * 70)

    # ========================================================
    # CSV
    # ========================================================

    fieldnames = [
        "Mode",
        "TP",
        "SL",
        "RR",
        "Signals",
        "Resolved",
        "Wins",
        "Losses",
        "FridayClose",
        "WR_%",
        "PF",
        "NetR",
        "DD_%",
        "MaxSL",
        "AvgDays",
        "MaxDays",
        "MaxOpen",
    ]

    with open(
        OUTPUT_FILE,
        "w",
        newline="",
        encoding="utf-8"
    ) as f:

        writer = csv.DictWriter(
            f,
            fieldnames=fieldnames
        )

        writer.writeheader()

        for r in all_results:
            writer.writerow({
                k: r.get(k, "")
                for k in fieldnames
            })

    print(f"Saved: {OUTPUT_FILE}")

    # ========================================================
    # TOP RESULTS
    # ========================================================

    # Sort primarily by Net R
    top_net = sorted(
        all_results,
        key=lambda x: x["NetR"],
        reverse=True
    )[:100]

    with open(
        TOP_FILE,
        "w",
        newline="",
        encoding="utf-8"
    ) as f:

        writer = csv.DictWriter(
            f,
            fieldnames=fieldnames
        )

        writer.writeheader()

        for r in top_net:
            writer.writerow({
                k: r.get(k, "")
                for k in fieldnames
            })

    print(f"Saved: {TOP_FILE}")

    # ========================================================
    # PRINT TOP 20 NET R
    # ========================================================

    print()
    print("=" * 110)
    print("TOP 20 BY NET R")
    print("=" * 110)

    print(
        f"{'Mode':<17}"
        f"{'Time':<7}"
        f"{'TP':>5}"
        f"{'SL':>5}"
        f"{'RR':>7}"
        f"{'WR%':>8}"
        f"{'PF':>8}"
        f"{'NetR':>10}"
        f"{'DD%':>9}"
        f"{'MaxSL':>7}"
        f"{'AvgD':>8}"
        f"{'MaxD':>8}"
    )

    for r in top_net[:20]:

        print(
            f"{r['Mode']:<17}"
            f"{SIGNAL_HOUR:02d}:{SIGNAL_MINUTE:02d} "
            f"{r['TP']:>5}"
            f"{r['SL']:>5}"
            f"{r['RR']:>7.2f}"
            f"{r['WR_%']:>8.2f}"
            f"{r['PF']:>8.3f}"
            f"{r['NetR']:>10.2f}"
            f"{r['DD_%']:>9.2f}"
            f"{r['MaxSL']:>7}"
            f"{r['AvgDays']:>8.3f}"
            f"{r['MaxDays']:>8.2f}"
        )

    # ========================================================
    # TOP LOW DD
    # ========================================================

    low_dd = sorted(
        [
            r for r in all_results
            if r["PF"] >= 1.0
            and r["NetR"] > 0
        ],
        key=lambda x: (
            x["DD_%"],
            -x["PF"],
            -x["NetR"]
        )
    )[:20]

    print()
    print("=" * 110)
    print("TOP 20 LOW DD (PF >= 1.0 AND NET R > 0)")
    print("=" * 110)

    for r in low_dd:

        print(
            f"{r['Mode']:<17}"
            f"{SIGNAL_HOUR:02d}:{SIGNAL_MINUTE:02d} "
            f"TP={r['TP']:>3} "
            f"SL={r['SL']:>3} "
            f"RR={r['RR']:.2f} "
            f"WR={r['WR_%']:.2f}% "
            f"PF={r['PF']:.3f} "
            f"NetR={r['NetR']:.2f} "
            f"DD={r['DD_%']:.2f}% "
            f"MaxSL={r['MaxSL']}"
        )

    # ========================================================
    # HIGH WR
    # ========================================================

    high_wr = sorted(
        [
            r for r in all_results
            if r["PF"] >= 1.0
            and r["NetR"] > 0
        ],
        key=lambda x: (
            -x["WR_%"],
            x["DD_%"]
        )
    )[:20]

    print()
    print("=" * 110)
    print("TOP 20 HIGH WIN RATE")
    print("=" * 110)

    for r in high_wr:

        print(
            f"{r['Mode']:<17}"
            f"TP={r['TP']:>3} "
            f"SL={r['SL']:>3} "
            f"RR={r['RR']:.2f} "
            f"WR={r['WR_%']:.2f}% "
            f"PF={r['PF']:.3f} "
            f"NetR={r['NetR']:.2f} "
            f"DD={r['DD_%']:.2f}% "
            f"MaxSL={r['MaxSL']}"
        )

    print()
    print("DONE.")


if __name__ == "__main__":
    main()

