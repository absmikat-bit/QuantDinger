import csv
from collections import defaultdict
from datetime import datetime
from pathlib import Path

CSV_FILE = "FTMO XAUUSD_M1_202208290105_202609302349.csv"

START_MINUTE = 65       # 01:05
END_MINUTE = 22 * 60     # 22:00

TP_PIPS = 85
SL_PIPS = 50
PIP_SIZE = 0.1

INITIAL_BALANCE = 25000.0
RISK_PERCENT = 0.7
RISK_MONEY = INITIAL_BALANCE * RISK_PERCENT / 100.0

OUTPUT = "ftmo_global_time_scan_85_50.csv"


def load_bars(path):
    bars = []

    with open(path, "r", encoding="utf-8-sig") as f:
        next(f, None)

        for line in f:
            p = line.split()

            if len(p) < 6:
                continue

            try:
                dt = datetime.strptime(
                    p[0] + " " + p[1],
                    "%Y.%m.%d %H:%M:%S"
                )

                bars.append({
                    "dt": dt,
                    "open": float(p[2]),
                    "high": float(p[3]),
                    "low": float(p[4]),
                    "close": float(p[5]),
                })

            except Exception:
                continue

    bars.sort(key=lambda x: x["dt"])
    return bars


def build_date_ids(bars):
    ids = []
    last_date = None
    current = -1

    for b in bars:
        d = b["dt"].date()

        if d != last_date:
            current += 1
            last_date = d

        ids.append(current)

    return ids


def test_time(bars, date_ids, signal_indices, minute_of_day):

    tp_distance = TP_PIPS * PIP_SIZE
    sl_distance = SL_PIPS * PIP_SIZE

    wins = 0
    losses = 0
    pending = 0

    net_r = 0.0
    gross_profit = 0.0
    gross_loss = 0.0

    trades = []

    for entry_i in signal_indices:

        entry = bars[entry_i]
        entry_price = entry["close"]

        tp = entry_price + tp_distance
        sl = entry_price - sl_distance

        status = "PENDING"
        exit_i = None
        exit_price = None

        for j in range(entry_i + 1, len(bars)):

            b = bars[j]

            hit_tp = b["high"] >= tp
            hit_sl = b["low"] <= sl

            # Same candle = SL first
            if hit_tp and hit_sl:
                status = "SL"
                exit_price = sl
                exit_i = j
                break

            if hit_sl:
                status = "SL"
                exit_price = min(sl, b["open"])
                exit_i = j
                break

            if hit_tp:
                status = "TP"
                exit_price = max(tp, b["open"])
                exit_i = j
                break

        if exit_i is None:

            pending += 1

            trades.append({
                "entry_i": entry_i,
                "exit_i": None,
                "r": 0.0,
                "status": "PENDING",
            })

            continue

        r = (exit_price - entry_price) / sl_distance

        if status == "TP":
            wins += 1
            gross_profit += r
        else:
            losses += 1
            gross_loss += abs(r)

        net_r += r

        trades.append({
            "entry_i": entry_i,
            "exit_i": exit_i,
            "r": r,
            "status": status,
        })

    resolved = wins + losses

    wr = (
        wins / resolved * 100
        if resolved else 0
    )

    pf = (
        gross_profit / gross_loss
        if gross_loss > 0 else 0
    )

    # --------------------------------------------------------
    # MAX CONSECUTIVE LOSSES
    # --------------------------------------------------------

    ordered = sorted(
        [t for t in trades if t["exit_i"] is not None],
        key=lambda x: x["exit_i"]
    )

    current_loss = 0
    max_loss = 0

    for t in ordered:

        if t["r"] < 0:
            current_loss += 1
            max_loss = max(max_loss, current_loss)
        else:
            current_loss = 0

    # --------------------------------------------------------
    # CLOSED TRADE DD
    # --------------------------------------------------------

    equity = 0.0
    peak = 0.0
    max_dd = 0.0

    for t in ordered:

        equity += t["r"]
        peak = max(peak, equity)

        dd = peak - equity
        max_dd = max(max_dd, dd)

    dd_percent = (
        max_dd * RISK_MONEY
        / INITIAL_BALANCE
        * 100
    )

    # --------------------------------------------------------
    # HOLDING DAYS
    # --------------------------------------------------------

    holding_days = []

    for t in ordered:

        entry_date = bars[t["entry_i"]]["dt"].date()
        exit_date = bars[t["exit_i"]]["dt"].date()

        holding_days.append(
            (exit_date - entry_date).days
        )

    avg_days = (
        sum(holding_days) / len(holding_days)
        if holding_days else 0
    )

    max_days = (
        max(holding_days)
        if holding_days else 0
    )

    # --------------------------------------------------------
    # MAX SIMULTANEOUS OPEN POSITIONS
    # --------------------------------------------------------

    events = []

    for t in trades:

        start = t["entry_i"]

        if t["exit_i"] is None:
            end = len(bars) - 1
        else:
            end = t["exit_i"]

        events.append((start, 1))
        events.append((end, -1))

    events.sort()

    active = 0
    max_open = 0

    for _, change in events:

        active += change
        max_open = max(max_open, active)

    hour = minute_of_day // 60
    minute = minute_of_day % 60

    return {
        "EntryTime": f"{hour:02d}:{minute:02d}",
        "Signals": len(trades),
        "Wins": wins,
        "Losses": losses,
        "Pending": pending,
        "WinRate": round(wr, 2),
        "PF": round(pf, 4),
        "NetR": round(net_r, 3),
        "MaxDD_R": round(max_dd, 3),
        "MaxDD_%": round(dd_percent, 3),
        "MaxSL": max_loss,
        "AvgDays": round(avg_days, 2),
        "MaxDays": max_days,
        "MaxOpen": max_open,
    }


def main():

    path = Path(CSV_FILE)

    if not path.exists():
        print("ERROR: CSV not found")
        return

    print("Loading FTMO XAUUSD M1 data...")

    bars = load_bars(path)

    print(f"Loaded bars: {len(bars):,}")

    date_ids = build_date_ids(bars)

    signals = defaultdict(list)

    for i, b in enumerate(bars):

        dt = b["dt"]

        minute_of_day = dt.hour * 60 + dt.minute

        if START_MINUTE <= minute_of_day <= END_MINUTE:

            signals[minute_of_day].append(i)

    results = []

    total = END_MINUTE - START_MINUTE + 1

    print()
    print("=" * 120)
    print("FTMO XAUUSD GLOBAL ENTRY-TIME SCAN")
    print("=" * 120)
    print("Entry     : 01:05 -> 22:00")
    print("Direction : BUY")
    print("TP        : 85 pips")
    print("SL        : 50 pips")
    print("RR        : 1.70")
    print("Forced    : OFF")
    print("=" * 120)
    print()

    for n, minute in enumerate(
        range(START_MINUTE, END_MINUTE + 1),
        1
    ):

        if not signals.get(minute):
            continue

        result = test_time(
            bars,
            date_ids,
            signals[minute],
            minute
        )

        results.append(result)

        if n % 100 == 0 or n == total:

            print(
                f"Progress: {n}/{total} "
                f"({100*n/total:.1f}%)"
            )

    # --------------------------------------------------------
    # SAVE
    # --------------------------------------------------------

    with open(
        OUTPUT,
        "w",
        newline="",
        encoding="utf-8-sig"
    ) as f:

        writer = csv.DictWriter(
            f,
            fieldnames=results[0].keys()
        )

        writer.writeheader()
        writer.writerows(results)

    # --------------------------------------------------------
    # TOP BY NET R
    # --------------------------------------------------------

    top_net = sorted(
        results,
        key=lambda x: x["NetR"],
        reverse=True
    )

    # --------------------------------------------------------
    # RISK-FIRST
    # --------------------------------------------------------

    risk_first = sorted(
        results,
        key=lambda x: (
            x["MaxDD_%"],
            x["MaxSL"],
            -x["PF"],
            -x["NetR"]
        )
    )

    print()
    print("=" * 120)
    print("TOP 20 BY NET R")
    print("=" * 120)

    print(
        f"{'Time':>6} "
        f"{'Sig':>5} "
        f"{'W':>5} "
        f"{'L':>5} "
        f"{'WR%':>7} "
        f"{'PF':>7} "
        f"{'NetR':>9} "
        f"{'DD%':>8} "
        f"{'MaxSL':>7} "
        f"{'AvgD':>7} "
        f"{'MaxD':>7} "
        f"{'MaxOpen':>8}"
    )

    print("-" * 120)

    for r in top_net[:20]:

        print(
            f"{r['EntryTime']:>6} "
            f"{r['Signals']:>5} "
            f"{r['Wins']:>5} "
            f"{r['Losses']:>5} "
            f"{r['WinRate']:>7.2f} "
            f"{r['PF']:>7.3f} "
            f"{r['NetR']:>9.2f} "
            f"{r['MaxDD_%']:>8.2f} "
            f"{r['MaxSL']:>7} "
            f"{r['AvgDays']:>7.2f} "
            f"{r['MaxDays']:>7} "
            f"{r['MaxOpen']:>8}"
        )

    print()
    print("=" * 120)
    print("TOP 20 RISK-FIRST")
    print("=" * 120)

    print(
        f"{'Time':>6} "
        f"{'Sig':>5} "
        f"{'W':>5} "
        f"{'L':>5} "
        f"{'WR%':>7} "
        f"{'PF':>7} "
        f"{'NetR':>9} "
        f"{'DD%':>8} "
        f"{'MaxSL':>7} "
        f"{'AvgD':>7} "
        f"{'MaxD':>7} "
        f"{'MaxOpen':>8}"
    )

    print("-" * 120)

    for r in risk_first[:20]:

        print(
            f"{r['EntryTime']:>6} "
            f"{r['Signals']:>5} "
            f"{r['Wins']:>5} "
            f"{r['Losses']:>5} "
            f"{r['WinRate']:>7.2f} "
            f"{r['PF']:>7.3f} "
            f"{r['NetR']:>9.2f} "
            f"{r['MaxDD_%']:>8.2f} "
            f"{r['MaxSL']:>7} "
            f"{r['AvgDays']:>7.2f} "
            f"{r['MaxDays']:>7} "
            f"{r['MaxOpen']:>8}"
        )

    print()
    print("=" * 80)
    print("SCAN COMPLETE")
    print("=" * 80)
    print(f"Times tested : {len(results)}")
    print(f"CSV saved    : {OUTPUT}")
    print()
    print("IMPORTANT:")
    print("This is a TIME DISCOVERY scan.")
    print("TP/SL are fixed at 85/50.")
    print("No swap, commission or floating DD yet.")


if __name__ == "__main__":
    main()
