import csv
from datetime import datetime, timedelta

CSV_FILE = "FTMO XAUUSD_M1_202208290105_202609302349.csv"

OOS_START = datetime(2026, 1, 1)
OOS_END   = datetime(2026, 9, 30, 23, 59)

RISK = 0.007
R_MONEY = 25000 * RISK

SETUPS = [
    ("21:03", 100, 65),
    ("21:45", 100, 65),
    ("20:28", 100, 60),
    ("22:00", 90, 45),
]

def parse_dt(row):
    return datetime.strptime(
        row["<DATE>"] + " " + row["<TIME>"],
        "%Y.%m.%d %H:%M:%S"
    )

def test_setup(bars, entry_h, entry_m, tp_pips, sl_pips, force_friday):
    trades = []

    # group indexes by date
    by_date = {}
    for i, b in enumerate(bars):
        by_date.setdefault(b["dt"].date(), []).append(i)

    for day, indices in by_date.items():
        if day < OOS_START.date() or day > OOS_END.date():
            continue

        entry_i = None
        for i in indices:
            dt = bars[i]["dt"]
            if dt.hour == entry_h and dt.minute == entry_m:
                entry_i = i
                break

        if entry_i is None:
            continue

        b = bars[entry_i]

        # FTMO XAUUSD CSV is 2-digit; spread is points of 0.01
        entry = b["close"] + b["spread"] * 0.01

        tp = entry + tp_pips * 0.1
        sl = entry - sl_pips * 0.1

        exit_i = None
        exit_price = None
        result = None

        for j in range(entry_i + 1, len(bars)):
            x = bars[j]
            dt = x["dt"]

            # Do not cross beyond OOS period
            if dt > OOS_END:
                break

            # Friday forced close at 23:49
            if force_friday and dt.date().weekday() == 4 and dt.hour == 23 and dt.minute == 49:
                exit_i = j
                exit_price = x["close"]
                result = "FRIDAY_CLOSE"
                break

            # If next date, continue holding
            if x["low"] <= sl and x["high"] >= tp:
                # Same-bar rule: SL first
                exit_i = j
                exit_price = sl
                result = "SL"
                break

            if x["low"] <= sl:
                exit_i = j
                exit_price = sl
                result = "SL"
                break

            if x["high"] >= tp:
                exit_i = j
                exit_price = tp
                result = "TP"
                break

        if exit_i is None:
            continue

        if result == "TP":
            r = tp_pips / sl_pips
        elif result == "SL":
            r = -1.0
        else:
            # Friday close result based on actual exit price
            r = (exit_price - entry) / (sl_pips * 0.1)

        trades.append({
            "entry_dt": b["dt"],
            "exit_dt": bars[exit_i]["dt"],
            "r": r,
            "result": result,
        })

    # statistics
    wins = sum(1 for t in trades if t["r"] > 0)
    losses = sum(1 for t in trades if t["r"] < 0)
    n = len(trades)

    gross_profit = sum(t["r"] for t in trades if t["r"] > 0)
    gross_loss = -sum(t["r"] for t in trades if t["r"] < 0)

    pf = gross_profit / gross_loss if gross_loss else 0
    net_r = sum(t["r"] for t in trades)
    wr = wins / n * 100 if n else 0

    # closed equity DD in R
    equity = 0
    peak = 0
    max_dd = 0

    for t in trades:
        equity += t["r"]
        peak = max(peak, equity)
        max_dd = max(max_dd, peak - equity)

    max_sl = 0
    streak = 0

    for t in trades:
        if t["r"] < 0:
            streak += 1
            max_sl = max(max_sl, streak)
        else:
            streak = 0

    friday_closes = sum(1 for t in trades if t["result"] == "FRIDAY_CLOSE")

    holding_days = [
        (t["exit_dt"] - t["entry_dt"]).total_seconds() / 86400
        for t in trades
    ]

    avg_days = sum(holding_days) / len(holding_days) if holding_days else 0
    max_days = max(holding_days) if holding_days else 0

    return {
        "signals": n,
        "wins": wins,
        "losses": losses,
        "friday_close": friday_closes,
        "wr": wr,
        "pf": pf,
        "net_r": net_r,
        "dd_r": max_dd,
        "dd_pct": max_dd * RISK * 100,
        "max_sl": max_sl,
        "avg_days": avg_days,
        "max_days": max_days,
    }


print("Loading FTMO M1 data...")

bars = []

with open(CSV_FILE, encoding="utf-8-sig", newline="") as f:
    reader = csv.DictReader(f, delimiter="\t")

    for row in reader:
        dt = parse_dt(row)

        bars.append({
            "dt": dt,
            "open": float(row["<OPEN>"]),
            "high": float(row["<HIGH>"]),
            "low": float(row["<LOW>"]),
            "close": float(row["<CLOSE>"]),
            "spread": float(row["<SPREAD>"]),
        })

print("Loaded bars:", len(bars))
print("OOS:", OOS_START, "to", OOS_END)
print()

for force in [True, False]:

    print("=" * 120)
    print("MODE:", "FRIDAY_CLOSE" if force else "NO_FORCE_CLOSE")
    print("=" * 120)

    print(
        f'{"TIME":>5} {"TP":>4} {"SL":>4} {"RR":>6} '
        f'{"SIG":>5} {"WR%":>7} {"PF":>7} {"NetR":>9} '
        f'{"DD%":>7} {"MaxSL":>6} {"AvgD":>7} {"MaxD":>6} {"Fri":>5}'
    )

    for time_str, tp, sl in SETUPS:
        h, m = map(int, time_str.split(":"))

        r = test_setup(
            bars,
            h,
            m,
            tp,
            sl,
            force
        )

        rr = tp / sl

        print(
            f'{time_str:>5} {tp:>4} {sl:>4} {rr:>6.2f} '
            f'{r["signals"]:>5} {r["wr"]:>7.2f} {r["pf"]:>7.3f} '
            f'{r["net_r"]:>9.2f} {r["dd_pct"]:>7.2f} '
            f'{r["max_sl"]:>6} {r["avg_days"]:>7.3f} '
            f'{r["max_days"]:>6.2f} {r["friday_close"]:>5}'
        )

print()
print("OOS TEST COMPLETE")
