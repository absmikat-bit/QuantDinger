import csv
from datetime import datetime

CSV_FILE = "FTMO XAUUSD_M1_202208290105_202609302349.csv"

# =========================
# PERIODS
# =========================
IS_START  = datetime(2022, 8, 29)
IS_END    = datetime(2024, 12, 31, 23, 59)

OOS_START = datetime(2025, 1, 1)
OOS_END   = datetime(2025, 12, 31, 23, 59)

# =========================
# SEARCH SPACE
# =========================
ENTRY_START = 18 * 60
ENTRY_END   = 22 * 60

TP_VALUES = range(50, 101, 5)
SL_VALUES = range(30, 101, 5)

MIN_RR = 1.5
MAX_RR = 2.0

PIP_SIZE = 0.1

# 25k account, 0.7% risk
RISK = 0.007

# Friday close model
FRIDAY_CLOSE = True


def parse_dt(row):
    return datetime.strptime(
        row["<DATE>"] + " " + row["<TIME>"],
        "%Y.%m.%d %H:%M:%S"
    )


def load_data():
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
    return bars


def run_setup(bars, start_dt, end_dt, entry_minute, tp_pips, sl_pips):
    trades = []

    # date -> indexes
    by_date = {}

    for i, b in enumerate(bars):
        dt = b["dt"]

        if start_dt <= dt <= end_dt:
            by_date.setdefault(dt.date(), []).append(i)

    entry_h = entry_minute // 60
    entry_m = entry_minute % 60

    for day, indices in by_date.items():

        entry_i = None

        for i in indices:
            dt = bars[i]["dt"]

            if dt.hour == entry_h and dt.minute == entry_m:
                entry_i = i
                break

        if entry_i is None:
            continue

        b = bars[entry_i]

        # BUY entry + FTMO spread adjustment
        entry = b["close"] + b["spread"] * 0.01

        tp = entry + tp_pips * PIP_SIZE
        sl = entry - sl_pips * PIP_SIZE

        exit_i = None
        exit_price = None
        status = None

        for j in range(entry_i + 1, len(bars)):

            x = bars[j]
            dt = x["dt"]

            # Do not allow trade beyond requested test period
            if dt > end_dt:
                break

            # Friday market close model
            if (
                FRIDAY_CLOSE
                and dt.weekday() == 4
                and dt.hour == 23
                and dt.minute == 49
            ):
                exit_i = j
                exit_price = x["close"]
                status = "FRIDAY_CLOSE"
                break

            # Same-bar rule = SL first
            if x["low"] <= sl and x["high"] >= tp:
                exit_i = j
                exit_price = sl
                status = "SL"
                break

            if x["low"] <= sl:
                exit_i = j
                exit_price = sl
                status = "SL"
                break

            if x["high"] >= tp:
                exit_i = j
                exit_price = tp
                status = "TP"
                break

        if exit_i is None:
            continue

        if status == "TP":
            r = tp_pips / sl_pips

        elif status == "SL":
            r = -1.0

        else:
            r = (exit_price - entry) / (sl_pips * PIP_SIZE)

        trades.append({
            "entry_dt": b["dt"],
            "exit_dt": bars[exit_i]["dt"],
            "r": r,
            "status": status,
        })

    return trades


def statistics(trades):
    if not trades:
        return {
            "signals": 0,
            "wins": 0,
            "losses": 0,
            "friday": 0,
            "wr": 0,
            "pf": 0,
            "net_r": 0,
            "dd_r": 0,
            "dd_pct": 0,
            "max_sl": 0,
            "avg_days": 0,
            "max_days": 0,
        }

    wins = sum(1 for t in trades if t["r"] > 0)
    losses = sum(1 for t in trades if t["r"] < 0)

    gross_profit = sum(
        t["r"] for t in trades if t["r"] > 0
    )

    gross_loss = -sum(
        t["r"] for t in trades if t["r"] < 0
    )

    pf = gross_profit / gross_loss if gross_loss else 0

    net_r = sum(t["r"] for t in trades)

    wr = wins / len(trades) * 100

    # Closed-equity DD in R
    equity = 0
    peak = 0
    max_dd = 0

    for t in trades:
        equity += t["r"]

        if equity > peak:
            peak = equity

        dd = peak - equity

        if dd > max_dd:
            max_dd = dd

    # Loss streak
    max_sl = 0
    streak = 0

    for t in trades:

        if t["r"] < 0:
            streak += 1
            max_sl = max(max_sl, streak)

        else:
            streak = 0

    holding_days = [
        (t["exit_dt"] - t["entry_dt"]).total_seconds()
        / 86400
        for t in trades
    ]

    avg_days = sum(holding_days) / len(holding_days)
    max_days = max(holding_days)

    friday = sum(
        1 for t in trades
        if t["status"] == "FRIDAY_CLOSE"
    )

    return {
        "signals": len(trades),
        "wins": wins,
        "losses": losses,
        "friday": friday,
        "wr": wr,
        "pf": pf,
        "net_r": net_r,
        "dd_r": max_dd,
        "dd_pct": max_dd * RISK * 100,
        "max_sl": max_sl,
        "avg_days": avg_days,
        "max_days": max_days,
    }



def test_period(bars, start_dt, end_dt):
    results = []

    configs = []

    for minute in range(ENTRY_START, ENTRY_END + 1):
        for tp in TP_VALUES:
            for sl in SL_VALUES:
                rr = tp / sl

                if rr < MIN_RR or rr > MAX_RR:
                    continue

                configs.append((minute, tp, sl, rr))

    total = len(configs)

    print()
    print(f"IS Optimization: {total:,} configurations")
    print()

    import time
    start_time = time.time()

    for n, (minute, tp, sl, rr) in enumerate(configs, 1):

        trades = run_setup(
            bars,
            start_dt,
            end_dt,
            minute,
            tp,
            sl
        )

        s = statistics(trades)

        results.append({
            "minute": minute,
            "tp": tp,
            "sl": sl,
            "rr": rr,
            **s
        })

        if n == 1 or n % 50 == 0 or n == total:

            elapsed = time.time() - start_time
            percent = n / total * 100

            speed = n / elapsed if elapsed > 0 else 0
            remaining = (total - n) / speed if speed > 0 else 0

            bar_len = 30
            filled = int(bar_len * n / total)

            bar = "#" * filled + "-" * (bar_len - filled)

            mins = int(remaining // 60)
            secs = int(remaining % 60)

            message = (
                "IS Optimization: "
                f"[{bar}] "
                f"{percent:6.2f}% | "
                f"{n:,}/{total:,} | "
                f"Speed {speed:.1f}/s | "
                f"ETA {mins:02d}:{secs:02d}"
            )

            print(message, end="\r", flush=True)

    print()
    print()

    return results

def fmt_time(minute):
    return f"{minute // 60:02d}:{minute % 60:02d}"


# ============================================================
# MAIN
# ============================================================

bars = load_data()

print()
print("=" * 110)
print("IN-SAMPLE OPTIMIZATION")
print("=" * 110)
print("Period:", IS_START, "to", IS_END)

is_results = test_period(
    bars,
    IS_START,
    IS_END
)

# Rank by Net R, but require PF > 1
valid = [
    r for r in is_results
    if r["pf"] > 1.0
    and r["net_r"] > 0
]

valid.sort(
    key=lambda r: (
        r["net_r"],
        r["pf"]
    ),
    reverse=True
)

print()
print("Total configurations:", len(is_results))
print("Valid configurations:", len(valid))

print()
print("TOP 20 IS RESULTS")
print(
    f'{"TIME":>5} {"TP":>4} {"SL":>4} {"RR":>5} '
    f'{"SIG":>5} {"WR%":>7} {"PF":>7} '
    f'{"NetR":>9} {"DD%":>7} {"MaxSL":>6}'
)

for r in valid[:20]:

    print(
        f'{fmt_time(r["minute"]):>5} '
        f'{r["tp"]:>4} '
        f'{r["sl"]:>4} '
        f'{r["rr"]:>5.2f} '
        f'{r["signals"]:>5} '
        f'{r["wr"]:>7.2f} '
        f'{r["pf"]:>7.3f} '
        f'{r["net_r"]:>9.2f} '
        f'{r["dd_pct"]:>7.2f} '
        f'{r["max_sl"]:>6}'
    )


# ============================================================
# WALK-FORWARD OOS
# ============================================================

print()
print("=" * 110)
print("WALK-FORWARD OOS TEST")
print("=" * 110)
print("OOS Period:", OOS_START, "to", OOS_END)

# Test ONLY the top 20 IS-selected configurations.
# NO re-optimization inside OOS.

oos_results = []

for r in valid[:20]:

    trades = run_setup(
        bars,
        OOS_START,
        OOS_END,
        r["minute"],
        r["tp"],
        r["sl"]
    )

    s = statistics(trades)

    oos_results.append({
        "minute": r["minute"],
        "tp": r["tp"],
        "sl": r["sl"],
        "rr": r["rr"],
        "is_net_r": r["net_r"],
        "is_pf": r["pf"],
        "is_dd": r["dd_pct"],
        **s
    })


print()
print("OOS RESULTS")
print(
    f'{"TIME":>5} {"TP":>4} {"SL":>4} {"RR":>5} '
    f'{"SIG":>5} {"WR%":>7} {"PF":>7} '
    f'{"NetR":>9} {"DD%":>7} {"MaxSL":>6} '
    f'{"IS_R":>9} {"IS_PF":>7}'
)

for r in oos_results:

    print(
        f'{fmt_time(r["minute"]):>5} '
        f'{r["tp"]:>4} '
        f'{r["sl"]:>4} '
        f'{r["rr"]:>5.2f} '
        f'{r["signals"]:>5} '
        f'{r["wr"]:>7.2f} '
        f'{r["pf"]:>7.3f} '
        f'{r["net_r"]:>9.2f} '
        f'{r["dd_pct"]:>7.2f} '
        f'{r["max_sl"]:>6} '
        f'{r["is_net_r"]:>9.2f} '
        f'{r["is_pf"]:>7.3f}'
    )


# Save
with open(
    "walk_forward_2022_2025_results.csv",
    "w",
    newline="",
    encoding="utf-8"
) as f:

    fields = [
        "minute",
        "tp",
        "sl",
        "rr",
        "signals",
        "wins",
        "losses",
        "friday",
        "wr",
        "pf",
        "net_r",
        "dd_r",
        "dd_pct",
        "max_sl",
        "avg_days",
        "max_days",
        "is_net_r",
        "is_pf",
        "is_dd",
    ]

    writer = csv.DictWriter(f, fieldnames=fields)
    writer.writeheader()

    for r in oos_results:
        writer.writerow(r)

print()
print("Saved: walk_forward_2022_2025_results.csv")
print()
print("WALK-FORWARD TEST COMPLETE")
