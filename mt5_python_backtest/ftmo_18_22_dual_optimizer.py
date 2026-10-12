import csv
import math
from collections import defaultdict
from datetime import datetime, timedelta
from pathlib import Path

# ============================================================
# CONFIG
# ============================================================

CSV_FILE = "FTMO XAUUSD_M1_202208290105_202609302349.csv"

START_MINUTE = 18 * 60
END_MINUTE = 22 * 60

TP_START = 50
TP_END = 100
TP_STEP = 5

SL_START = 30
SL_END = 100
SL_STEP = 5

MIN_RR = 1.5
MAX_RR = 2.0

PIP_SIZE = 0.1

INITIAL_BALANCE = 25000.0
RISK_PERCENT = 0.7
RISK_MONEY = INITIAL_BALANCE * RISK_PERCENT / 100.0

# Friday FTMO XAUUSD last available M1 bar = 23:49.
# We force-close at the last Friday bar's CLOSE.
FRIDAY_CLOSE_HOUR = 23
FRIDAY_CLOSE_MINUTE = 49

OUTPUT_ALL = "ftmo_18_22_dual_all_results.csv"
OUTPUT_TOP = "ftmo_18_22_dual_top_results.csv"
OUTPUT_AUDIT = "ftmo_18_22_dual_top_trade_audit.csv"


# ============================================================
# LOAD DATA
# ============================================================

def load_bars(path):
    bars = []

    print("Loading FTMO XAUUSD M1 data...")

    with open(path, "r", encoding="utf-8-sig") as f:
        header = next(f, None)

        for line in f:
            p = line.split()

            if len(p) < 9:
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
                    "tickvol": int(float(p[6])),
                    "vol": int(float(p[7])),
                    "spread": float(p[8]),
                })

            except Exception:
                continue

    bars.sort(key=lambda x: x["dt"])

    print(f"Loaded bars: {len(bars):,}")

    return bars


# ============================================================
# SIGNALS
# ============================================================

def build_signals(bars):

    signals = defaultdict(list)

    for i, b in enumerate(bars):

        dt = b["dt"]

        minute_of_day = dt.hour * 60 + dt.minute

        if START_MINUTE <= minute_of_day <= END_MINUTE:

            # Only one signal per date/time.
            signals[minute_of_day].append(i)

    return signals


# ============================================================
# GAP-AWARE EXIT
# ============================================================

def resolve_trade(
    bars,
    entry_i,
    tp_distance,
    sl_distance,
    friday_force_close
):

    entry_bar = bars[entry_i]

    # FTMO CSV XAUUSD has 2 decimal prices.
    # spread field is points of 0.01.
    spread_price = entry_bar["spread"] * 0.01

    # BUY enters at ASK.
    entry_price = entry_bar["close"] + spread_price

    tp = entry_price + tp_distance
    sl = entry_price - sl_distance

    entry_dt = entry_bar["dt"]

    for j in range(entry_i + 1, len(bars)):

        b = bars[j]
        dt = b["dt"]

        # ----------------------------------------------------
        # Friday force close
        # ----------------------------------------------------

        if friday_force_close:

            if (
                dt.weekday() == 4
                and dt.hour == FRIDAY_CLOSE_HOUR
                and dt.minute == FRIDAY_CLOSE_MINUTE
            ):

                # If TP/SL is hit on the Friday close bar,
                # apply normal TP/SL logic first.
                hit_tp = b["high"] >= tp
                hit_sl = b["low"] <= sl

                if hit_tp and hit_sl:
                    # Conservative: SL first.
                    exit_price = sl
                    status = "SL"

                elif hit_sl:
                    exit_price = min(sl, b["open"])
                    status = "SL"

                elif hit_tp:
                    exit_price = max(tp, b["open"])
                    status = "TP"

                else:
                    exit_price = b["close"]
                    status = "FRIDAY_CLOSE"

                r = (exit_price - entry_price) / sl_distance

                return {
                    "entry_i": entry_i,
                    "exit_i": j,
                    "entry_dt": entry_dt,
                    "exit_dt": dt,
                    "entry_price": entry_price,
                    "exit_price": exit_price,
                    "tp": tp,
                    "sl": sl,
                    "r": r,
                    "status": status,
                }

        # ----------------------------------------------------
        # Normal TP/SL
        # ----------------------------------------------------

        hit_tp = b["high"] >= tp
        hit_sl = b["low"] <= sl

        # Same-bar rule = SL first.
        if hit_tp and hit_sl:

            exit_price = sl
            status = "SL"

            r = (exit_price - entry_price) / sl_distance

            return {
                "entry_i": entry_i,
                "exit_i": j,
                "entry_dt": entry_dt,
                "exit_dt": dt,
                "entry_price": entry_price,
                "exit_price": exit_price,
                "tp": tp,
                "sl": sl,
                "r": r,
                "status": status,
            }

        if hit_sl:

            exit_price = min(sl, b["open"])
            status = "SL"

            r = (exit_price - entry_price) / sl_distance

            return {
                "entry_i": entry_i,
                "exit_i": j,
                "entry_dt": entry_dt,
                "exit_dt": dt,
                "entry_price": entry_price,
                "exit_price": exit_price,
                "tp": tp,
                "sl": sl,
                "r": r,
                "status": status,
            }

        if hit_tp:

            exit_price = max(tp, b["open"])
            status = "TP"

            r = (exit_price - entry_price) / sl_distance

            return {
                "entry_i": entry_i,
                "exit_i": j,
                "entry_dt": entry_dt,
                "exit_dt": dt,
                "exit_price": exit_price,
                "entry_price": entry_price,
                "tp": tp,
                "sl": sl,
                "r": r,
                "status": status,
            }

    # Data ended while trade still open.
    return None


# ============================================================
# FLOATING DD
# ============================================================

def calculate_floating_dd(
    bars,
    trades,
    sl_distance
):

    # Events indexed by M1 bar.
    starts = defaultdict(list)
    ends = defaultdict(list)

    for n, t in enumerate(trades):

        starts[t["entry_i"]].append(n)

        if t["exit_i"] is not None:
            ends[t["exit_i"]].append(n)

    active = set()

    max_floating_dd_r = 0.0
    max_total_exposure_r = 0.0

    # Only inspect bars between first entry and last exit.
    if not trades:
        return 0.0, 0.0

    first_i = min(t["entry_i"] for t in trades)
    last_i = max(
        t["exit_i"]
        for t in trades
        if t["exit_i"] is not None
    )

    for i in range(first_i, last_i + 1):

        # Add new positions.
        for n in starts.get(i, []):
            active.add(n)

        # ----------------------------------------------------
        # Mark-to-market using worst BUY price in each bar.
        # For floating DD, use bar LOW.
        # ----------------------------------------------------

        floating_r = 0.0

        for n in active:

            t = trades[n]

            # If this trade exits on this bar, it is no longer
            # an open trade after its exit.
            if t["exit_i"] is not None and t["exit_i"] <= i:
                continue

            adverse_price = bars[i]["low"]

            unrealized_r = (
                adverse_price - t["entry_price"]
            ) / sl_distance

            floating_r += unrealized_r

        if floating_r < 0:

            dd = abs(floating_r)

            max_floating_dd_r = max(
                max_floating_dd_r,
                dd
            )

        max_total_exposure_r = max(
            max_total_exposure_r,
            len(active)
        )

        # Remove closed positions.
        for n in ends.get(i, []):
            active.discard(n)

    max_floating_dd_percent = (
        max_floating_dd_r
        * RISK_MONEY
        / INITIAL_BALANCE
        * 100
    )

    return max_floating_dd_r, max_floating_dd_percent


# ============================================================
# MAX SIMULTANEOUS POSITIONS
# ============================================================

def calculate_max_open(trades):

    events = []

    for t in trades:

        events.append(
            (t["entry_i"], 1)
        )

        if t["exit_i"] is not None:

            events.append(
                (t["exit_i"], -1)
            )

    # Exit before entry at same bar.
    events.sort(
        key=lambda x: (
            x[0],
            0 if x[1] == -1 else 1
        )
    )

    active = 0
    maximum = 0

    for _, change in events:

        active += change
        maximum = max(maximum, active)

    return maximum


# ============================================================
# CLOSED DD
# ============================================================

def calculate_closed_dd(trades):

    resolved = [
        t for t in trades
        if t["exit_i"] is not None
    ]

    resolved.sort(
        key=lambda x: x["exit_i"]
    )

    equity_r = 0.0
    peak_r = 0.0
    max_dd_r = 0.0

    for t in resolved:

        equity_r += t["r"]

        peak_r = max(
            peak_r,
            equity_r
        )

        dd = peak_r - equity_r

        max_dd_r = max(
            max_dd_r,
            dd
        )

    dd_percent = (
        max_dd_r
        * RISK_MONEY
        / INITIAL_BALANCE
        * 100
    )

    return max_dd_r, dd_percent


# ============================================================
# MAX LOSING STREAK
# ============================================================

def calculate_max_sl(trades):

    resolved = [
        t for t in trades
        if t["exit_i"] is not None
    ]

    resolved.sort(
        key=lambda x: x["exit_i"]
    )

    current = 0
    maximum = 0

    for t in resolved:

        if t["status"] == "SL":

            current += 1

            maximum = max(
                maximum,
                current
            )

        else:

            current = 0

    return maximum


# ============================================================
# TEST ONE CONFIGURATION
# ============================================================

def test_configuration(
    bars,
    signal_indices,
    entry_minute,
    tp_pips,
    sl_pips,
    friday_force_close
):

    tp_distance = tp_pips * PIP_SIZE
    sl_distance = sl_pips * PIP_SIZE

    trades = []

    for entry_i in signal_indices:

        trade = resolve_trade(
            bars,
            entry_i,
            tp_distance,
            sl_distance,
            friday_force_close
        )

        if trade is not None:
            trades.append(trade)

    if not trades:
        return None, []

    wins = sum(
        1 for t in trades
        if t["status"] == "TP"
    )

    losses = sum(
        1 for t in trades
        if t["status"] == "SL"
    )

    forced = sum(
        1 for t in trades
        if t["status"] == "FRIDAY_CLOSE"
    )

    resolved = wins + losses + forced

    gross_profit = sum(
        max(0, t["r"])
        for t in trades
    )

    gross_loss = sum(
        abs(min(0, t["r"]))
        for t in trades
    )

    net_r = sum(
        t["r"]
        for t in trades
    )

    wr = (
        wins / resolved * 100
        if resolved else 0
    )

    pf = (
        gross_profit / gross_loss
        if gross_loss > 0 else 0
    )

    closed_dd_r, closed_dd_pct = calculate_closed_dd(
        trades
    )

    floating_dd_r, floating_dd_pct = calculate_floating_dd(
        bars,
        trades,
        sl_distance
    )

    max_sl = calculate_max_sl(
        trades
    )

    max_open = calculate_max_open(
        trades
    )

    holding_days = []

    trading_dates = []

    for t in trades:

        entry_date = t["entry_dt"].date()

        if t["exit_dt"] is not None:

            exit_date = t["exit_dt"].date()

            holding_days.append(
                (exit_date - entry_date).days
            )

        trading_dates.append(entry_date)

    avg_days = (
        sum(holding_days) / len(holding_days)
        if holding_days else 0
    )

    max_days = (
        max(holding_days)
        if holding_days else 0
    )

    result = {
        "Mode": (
            "FRIDAY_CLOSE"
            if friday_force_close
            else "NO_FORCE_CLOSE"
        ),
        "EntryTime": (
            f"{entry_minute // 60:02d}:"
            f"{entry_minute % 60:02d}"
        ),
        "TP": tp_pips,
        "SL": sl_pips,
        "RR": round(tp_pips / sl_pips, 4),

        "Signals": len(trades),
        "Wins": wins,
        "Losses": losses,
        "FridayClose": forced,

        "WR_%": round(wr, 3),
        "PF": round(pf, 4),
        "NetR": round(net_r, 3),

        "ClosedDD_R": round(closed_dd_r, 3),
        "ClosedDD_%": round(closed_dd_pct, 3),

        "FloatingDD_R": round(floating_dd_r, 3),
        "FloatingDD_%": round(floating_dd_pct, 3),

        "MaxSL": max_sl,
        "AvgDays": round(avg_days, 3),
        "MaxDays": max_days,
        "MaxOpen": max_open,
    }

    return result, trades


# ============================================================
# MAIN
# ============================================================

def main():

    if not Path(CSV_FILE).exists():

        print("ERROR: CSV file not found:")
        print(CSV_FILE)
        return

    bars = load_bars(
        CSV_FILE
    )

    signals = build_signals(
        bars
    )

    valid_pairs = []

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

            rr = tp / sl

            if (
                MIN_RR
                <= rr
                <= MAX_RR
            ):

                valid_pairs.append(
                    (tp, sl)
                )

    times = list(
        range(
            START_MINUTE,
            END_MINUTE + 1
        )
    )

    configs = (
        len(times)
        * len(valid_pairs)
    )

    print()
    print("=" * 120)
    print("FTMO 18:00-22:00 DUAL OPTIMIZER")
    print("=" * 120)
    print(f"Entry times       : {len(times):,}")
    print(f"TP/SL combinations: {len(valid_pairs):,}")
    print(f"Configurations    : {configs:,}")
    print("Direction         : BUY")
    print("TP                 : 50-100 step 5")
    print("SL                 : 30-100 step 5")
    print("RR                 : 1.5-2.0")
    print("Risk               : 0.7%")
    print("Friday test        : ON + OFF")
    print("Swap/commission    : NOT INCLUDED")
    print("=" * 120)

    all_results = []

    total = configs * 2
    completed = 0

    # --------------------------------------------------------
    # RUN BOTH MODES
    # --------------------------------------------------------

    for friday_force_close in (
        True,
        False
    ):

        mode_name = (
            "FRIDAY_CLOSE"
            if friday_force_close
            else "NO_FORCE_CLOSE"
        )

        print()
        print("=" * 80)
        print(f"RUNNING: {mode_name}")
        print("=" * 80)

        for entry_minute in times:

            indices = signals.get(
                entry_minute,
                []
            )

            if not indices:
                continue

            for tp, sl in valid_pairs:

                result, trades = test_configuration(
                    bars,
                    indices,
                    entry_minute,
                    tp,
                    sl,
                    friday_force_close
                )

                if result is not None:

                    all_results.append(
                        result
                    )

                completed += 1

                if completed % 1000 == 0:

                    print(
                        f"Progress: "
                        f"{completed:,}/{total:,} "
                        f"("
                        f"{completed/total*100:.1f}%"
                        f")"
                    )

    # --------------------------------------------------------
    # SAVE ALL RESULTS
    # --------------------------------------------------------

    if not all_results:

        print("No results.")
        return

    fields = list(
        all_results[0].keys()
    )

    with open(
        OUTPUT_ALL,
        "w",
        newline="",
        encoding="utf-8-sig"
    ) as f:

        writer = csv.DictWriter(
            f,
            fieldnames=fields
        )

        writer.writeheader()
        writer.writerows(
            all_results
        )

    # --------------------------------------------------------
    # TOP BY NET R
    # --------------------------------------------------------

    top_net = sorted(
        all_results,
        key=lambda x: x["NetR"],
        reverse=True
    )

    # --------------------------------------------------------
    # RISK-FIRST
    #
    # Floating DD is considered first.
    # --------------------------------------------------------

    risk_first = sorted(
        all_results,
        key=lambda x: (
            x["FloatingDD_%"],
            x["ClosedDD_%"],
            x["MaxSL"],
            -x["PF"],
            -x["NetR"]
        )
    )

    # --------------------------------------------------------
    # SAVE TOP 200
    # --------------------------------------------------------

    top_200 = top_net[:200]

    with open(
        OUTPUT_TOP,
        "w",
        newline="",
        encoding="utf-8-sig"
    ) as f:

        writer = csv.DictWriter(
            f,
            fieldnames=fields
        )

        writer.writeheader()
        writer.writerows(
            top_200
        )

    # --------------------------------------------------------
    # PRINT TOP 30
    # --------------------------------------------------------

    def print_table(
        title,
        rows
    ):

        print()
        print("=" * 150)
        print(title)
        print("=" * 150)

        print(
            f"{'Mode':<15}"
            f"{'Time':>6}"
            f"{'TP':>5}"
            f"{'SL':>5}"
            f"{'RR':>6}"
            f"{'WR%':>7}"
            f"{'PF':>7}"
            f"{'NetR':>9}"
            f"{'CDD%':>8}"
            f"{'FDD%':>8}"
            f"{'MaxSL':>7}"
            f"{'AvgD':>7}"
            f"{'MaxD':>7}"
            f"{'Open':>6}"
        )

        print("-" * 150)

        for r in rows:

            print(
                f"{r['Mode']:<15}"
                f"{r['EntryTime']:>6}"
                f"{r['TP']:>5}"
                f"{r['SL']:>5}"
                f"{r['RR']:>6.2f}"
                f"{r['WR_%']:>7.2f}"
                f"{r['PF']:>7.3f}"
                f"{r['NetR']:>9.2f}"
                f"{r['ClosedDD_%']:>8.2f}"
                f"{r['FloatingDD_%']:>8.2f}"
                f"{r['MaxSL']:>7}"
                f"{r['AvgDays']:>7.2f}"
                f"{r['MaxDays']:>7}"
                f"{r['MaxOpen']:>6}"
            )

    print_table(
        "TOP 30 BY NET R",
        top_net[:30]
    )

    print_table(
        "TOP 30 RISK-FIRST",
        risk_first[:30]
    )

    # --------------------------------------------------------
    # BEST EACH MODE
    # --------------------------------------------------------

    print()
    print("=" * 100)
    print("BEST RESULTS BY MODE")
    print("=" * 100)

    for mode in (
        "FRIDAY_CLOSE",
        "NO_FORCE_CLOSE"
    ):

        mode_rows = [
            r for r in all_results
            if r["Mode"] == mode
        ]

        mode_rows.sort(
            key=lambda x: x["NetR"],
            reverse=True
        )

        print()
        print(f"--- {mode} : TOP 10 NET R ---")

        for r in mode_rows[:10]:

            print(
                f"{r['EntryTime']} | "
                f"TP {r['TP']} SL {r['SL']} "
                f"RR {r['RR']:.2f} | "
                f"WR {r['WR_%']:.2f}% | "
                f"PF {r['PF']:.3f} | "
                f"NetR {r['NetR']:.2f} | "
                f"ClosedDD {r['ClosedDD_%']:.2f}% | "
                f"FloatingDD {r['FloatingDD_%']:.2f}% | "
                f"MaxSL {r['MaxSL']} | "
                f"AvgDays {r['AvgDays']:.2f} | "
                f"MaxDays {r['MaxDays']}"
            )

    print()
    print("=" * 100)
    print("COMPLETE")
    print("=" * 100)
    print(f"All results : {OUTPUT_ALL}")
    print(f"Top 200     : {OUTPUT_TOP}")
    print()
    print("NOTE:")
    print("Friday close = Friday 23:49 bar close.")
    print("No-force mode allows positions to remain open until TP/SL.")
    print("Swap and commission are NOT included.")
    print("Floating DD is based on intrabar BUY adverse excursion.")
    print("This is still an in-sample optimization; final candidates")
    print("must be validated out-of-sample before live/prop use.")


if __name__ == "__main__":
    main()
