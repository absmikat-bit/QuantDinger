from pathlib import Path
import csv
from datetime import datetime

CSV_FILE = "FTMO XAUUSD_M1_202208290105_202609302349.csv"

TP_PIPS = 85.0
SL_PIPS = 50.0
PIP_SIZE = 0.1

START_MINUTE = 37
END_MINUTE = 49


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
            except:
                pass

    return bars


def test_minute(bars, minute):

    by_date = {}

    for i, b in enumerate(bars):
        by_date.setdefault(b["dt"].date(), []).append(i)

    tp = TP_PIPS * PIP_SIZE
    sl = SL_PIPS * PIP_SIZE

    signals = 0
    wins = 0
    losses = 0
    forced = 0
    pending = 0
    net_r = 0.0

    for day, idxs in by_date.items():

        signal_i = None

        for i in idxs:
            dt = bars[i]["dt"]

            if dt.hour == 23 and dt.minute == minute:
                signal_i = i
                break

        if signal_i is None:
            continue

        signals += 1

        entry = bars[signal_i]["close"]
        target = entry + tp
        stop = entry - sl

        result = None

        # Only until 23:49
        for j in range(signal_i + 1, len(bars)):

            b = bars[j]
            dt = b["dt"]

            if dt.date() != day:
                break

            if dt.hour != 23 or dt.minute > 49:
                break

            hit_tp = b["high"] >= target
            hit_sl = b["low"] <= stop

            # Same bar: SL first
            if hit_tp and hit_sl:
                result = -1.0
                losses += 1
                break

            if hit_tp:
                result = TP_PIPS / SL_PIPS
                wins += 1
                break

            if hit_sl:
                result = -1.0
                losses += 1
                break

            # 23:49 close = forced close
            if dt.minute == 49:
                forced_r = (
                    (b["close"] - entry)
                    / (SL_PIPS * PIP_SIZE)
                )

                result = forced_r
                forced += 1
                break

        if result is None:
            pending += 1
        else:
            net_r += result

    gross_profit = 0.0
    gross_loss = 0.0

    # Recalculate PF using a second pass
    # by reconstructing result values
    #
    # For the scan, approximate PF from Net R is not enough,
    # so run detailed result collection.

    # Re-run for exact PF
    results = []

    for day, idxs in by_date.items():

        signal_i = None

        for i in idxs:
            dt = bars[i]["dt"]

            if dt.hour == 23 and dt.minute == minute:
                signal_i = i
                break

        if signal_i is None:
            continue

        entry = bars[signal_i]["close"]
        target = entry + tp
        stop = entry - sl

        result = None

        for j in range(signal_i + 1, len(bars)):

            b = bars[j]
            dt = b["dt"]

            if dt.date() != day:
                break

            if dt.hour != 23 or dt.minute > 49:
                break

            hit_tp = b["high"] >= target
            hit_sl = b["low"] <= stop

            if hit_tp and hit_sl:
                result = -1.0
                break

            if hit_tp:
                result = TP_PIPS / SL_PIPS
                break

            if hit_sl:
                result = -1.0
                break

            if dt.minute == 49:
                result = (
                    (b["close"] - entry)
                    / (SL_PIPS * PIP_SIZE)
                )
                break

        if result is not None:
            results.append(result)

    for r in results:
        if r > 0:
            gross_profit += r
        elif r < 0:
            gross_loss += -r

    pf = (
        gross_profit / gross_loss
        if gross_loss > 0
        else 0
    )

    wr = (
        wins / (wins + losses) * 100
        if wins + losses > 0
        else 0
    )

    return {
        "minute": minute,
        "signals": signals,
        "wins": wins,
        "losses": losses,
        "forced": forced,
        "pending": pending,
        "wr": wr,
        "net_r": net_r,
        "pf": pf,
    }


print("Loading FTMO XAUUSD M1 data...")

bars = load_bars(CSV_FILE)

print(f"Loaded bars: {len(bars):,}")
print()

print("=" * 110)
print("FTMO XAUUSD — 23:37 TO 23:49 MINUTE SCAN")
print("BUY | TP85 | SL50 | PipSize 0.1 | Forced Close = 23:49 CLOSE")
print("=" * 110)

print()
print(
    f"{'Time':<8}"
    f"{'Signals':>9}"
    f"{'TP':>8}"
    f"{'SL':>8}"
    f"{'Forced':>10}"
    f"{'Pending':>10}"
    f"{'WR %':>10}"
    f"{'Net R':>12}"
    f"{'PF':>10}"
)

print("-" * 110)

all_results = []

for minute in range(START_MINUTE, END_MINUTE + 1):

    r = test_minute(
        bars,
        minute
    )

    all_results.append(r)

    print(
        f"23:{minute:02d}   "
        f"{r['signals']:>9}"
        f"{r['wins']:>8}"
        f"{r['losses']:>8}"
        f"{r['forced']:>10}"
        f"{r['pending']:>10}"
        f"{r['wr']:>9.2f}"
        f"{r['net_r']:>12.2f}"
        f"{r['pf']:>10.3f}"
    )

print("-" * 110)

best = sorted(
    all_results,
    key=lambda x: (
        x["net_r"],
        x["pf"]
    ),
    reverse=True
)

print()
print("TOP RESULTS BY NET R")
print("-" * 70)

for i, r in enumerate(best[:5], 1):

    print(
        f"{i}. 23:{r['minute']:02d} | "
        f"Net R {r['net_r']:.2f} | "
        f"PF {r['pf']:.3f} | "
        f"WR {r['wr']:.2f}% | "
        f"Forced {r['forced']}"
    )

print()
print("Scan complete.")
