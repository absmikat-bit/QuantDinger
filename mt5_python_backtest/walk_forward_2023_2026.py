import csv
import math
import os
import time
from datetime import datetime, timedelta
from collections import defaultdict


# ============================================================
# CONFIG
# ============================================================

DATA_FILE = "FTMO XAUUSD_M1_202208290105_202609302349.csv"

INITIAL_BALANCE = 25000.0
RISK_PERCENT = 0.7
RISK_MONEY = INITIAL_BALANCE * RISK_PERCENT / 100.0

PIP_SIZE = 0.1

ENTRY_START_MINUTE = 18 * 60
ENTRY_END_MINUTE = 22 * 60

TP_MIN = 50
TP_MAX = 100
TP_STEP = 5

SL_MIN = 30
SL_MAX = 100
SL_STEP = 5

MIN_RR = 1.5
MAX_RR = 2.0

TOP_N = 20

# Friday forced close at 23:49 candle close
FRIDAY_CLOSE = True

IS_START = datetime(2023, 1, 1)
IS_END = datetime(2025, 12, 31, 23, 59)

OOS_START = datetime(2026, 1, 1)
OOS_END = datetime(2026, 9, 30, 23, 59)

OUTPUT_FILE = "walk_forward_2023_2026_results.csv"


# ============================================================
# BAR
# ============================================================

class Bar:
    __slots__ = (
        "dt", "open", "high", "low", "close", "spread"
    )

    def __init__(self, dt, op, hi, lo, cl, spread):
        self.dt = dt
        self.open = op
        self.high = hi
        self.low = lo
        self.close = cl
        self.spread = spread


# ============================================================
# LOAD DATA
# ============================================================

def load_data(filename):

    print("Loading FTMO M1 data...")

    bars = []

    with open(filename, "r", encoding="utf-8-sig") as f:

        reader = csv.reader(f, delimiter="\t")

        header = next(reader)

        # Handle possible whitespace instead of tabs
        if len(header) < 5:
            f.seek(0)

            reader = csv.reader(
                f,
                delimiter=" ",
                skipinitialspace=True
            )

            header = next(reader)

        for row in reader:

            if not row:
                continue

            row = [x for x in row if x != ""]

            if len(row) < 9:
                continue

            try:

                dt = datetime.strptime(
                    row[0] + " " + row[1],
                    "%Y.%m.%d %H:%M"
                )

                op = float(row[2])
                hi = float(row[3])
                lo = float(row[4])
                cl = float(row[5])

                spread = float(row[8])

                bars.append(
                    Bar(
                        dt,
                        op,
                        hi,
                        lo,
                        cl,
                        spread
                    )
                )

            except Exception:
                continue

    bars.sort(key=lambda x: x.dt)

    print(f"Loaded bars: {len(bars)}")

    return bars


# ============================================================
# CONFIGURATION LIST
# ============================================================

def build_configs():

    configs = []

    for entry_minute in range(
        ENTRY_START_MINUTE,
        ENTRY_END_MINUTE + 1
    ):

        hour = entry_minute // 60
        minute = entry_minute % 60

        for tp in range(
            TP_MIN,
            TP_MAX + 1,
            TP_STEP
        ):

            for sl in range(
                SL_MIN,
                SL_MAX + 1,
                SL_STEP
            ):

                rr = tp / sl

                if rr < MIN_RR or rr > MAX_RR:
                    continue

                configs.append(
                    (
                        hour,
                        minute,
                        tp,
                        sl,
                        rr
                    )
                )

    return configs


# ============================================================
# FAST DATE INDEX
# ============================================================

def build_day_index(bars):

    days = defaultdict(list)

    for i, bar in enumerate(bars):
        days[bar.dt.date()].append(i)

    return days


# ============================================================
# BACKTEST
# ============================================================

def backtest(
    bars,
    days,
    config,
    start_dt,
    end_dt
):

    hour, minute, tp_pips, sl_pips, rr = config

    start_date = start_dt.date()
    end_date = end_dt.date()

    signals = 0
    wins = 0
    losses = 0
    friday_close = 0

    trades = []

    # --------------------------------------------------------
    # One signal per calendar day
    # --------------------------------------------------------

    current_date = start_date

    while current_date <= end_date:

        indexes = days.get(current_date)

        if not indexes:
            current_date += timedelta(days=1)
            continue

        # Find exact entry minute
        entry_index = None

        target_minute = hour * 60 + minute

        for idx in indexes:

            bar = bars[idx]

            m = bar.dt.hour * 60 + bar.dt.minute

            if m == target_minute:
                entry_index = idx
                break

        if entry_index is None:
            current_date += timedelta(days=1)
            continue

        entry_bar = bars[entry_index]

        # BUY entry with FTMO 2-digit XAUUSD spread
        entry_price = (
            entry_bar.close +
            entry_bar.spread * 0.01
        )

        tp_price = (
            entry_price +
            tp_pips * PIP_SIZE
        )

        sl_price = (
            entry_price -
            sl_pips * PIP_SIZE
        )

        signals += 1

        resolved = False

        exit_price = None
        result_r = None
        exit_dt = None
        result = None

        # ----------------------------------------------------
        # Search forward
        # ----------------------------------------------------

        j = entry_index + 1

        while j < len(bars):

            bar = bars[j]

            # Don't go beyond OOS/IS period
            if bar.dt > end_dt:
                break

            # ------------------------------------------------
            # Friday forced close
            # ------------------------------------------------

            if (
                FRIDAY_CLOSE
                and entry_bar.dt.weekday() == 4
                and bar.dt.date() == entry_bar.dt.date()
                and bar.dt.hour == 23
                and bar.dt.minute == 49
            ):

                # Check TP / SL on 23:49 bar first
                hit_sl = bar.low <= sl_price
                hit_tp = bar.high >= tp_price

                if hit_sl and hit_tp:

                    # Same-bar rule = SL first
                    exit_price = sl_price
                    result_r = -1.0
                    result = "SL"

                    losses += 1
                    resolved = True

                elif hit_tp:

                    exit_price = tp_price

                    result_r = rr
                    result = "TP"

                    wins += 1
                    resolved = True

                elif hit_sl:

                    exit_price = sl_price

                    result_r = -1.0
                    result = "SL"

                    losses += 1
                    resolved = True

                else:

                    # Force close at 23:49 close
                    exit_price = bar.close

                    result_r = (
                        exit_price - entry_price
                    ) / (sl_pips * PIP_SIZE)

                    result = "FRIDAY_CLOSE"

                    friday_close += 1
                    resolved = True

                exit_dt = bar.dt

                break

            # ------------------------------------------------
            # Normal TP / SL
            # ------------------------------------------------

            hit_sl = bar.low <= sl_price
            hit_tp = bar.high >= tp_price

            if hit_sl and hit_tp:

                # SL first
                exit_price = sl_price
                result_r = -1.0
                result = "SL"

                losses += 1
                resolved = True

                exit_dt = bar.dt

                break

            elif hit_tp:

                exit_price = tp_price
                result_r = rr
                result = "TP"

                wins += 1
                resolved = True

                exit_dt = bar.dt

                break

            elif hit_sl:

                exit_price = sl_price
                result_r = -1.0
                result = "SL"

                losses += 1
                resolved = True

                exit_dt = bar.dt

                break

            j += 1

        # ----------------------------------------------------
        # Pending trade
        # ----------------------------------------------------

        if resolved:

            trades.append(
                (
                    entry_bar.dt,
                    exit_dt,
                    result_r,
                    result
                )
            )

        current_date += timedelta(days=1)

    # ========================================================
    # METRICS
    # ========================================================

    resolved_count = len(trades)

    if resolved_count == 0:

        return {
            "signals": signals,
            "wins": 0,
            "losses": 0,
            "friday_close": friday_close,
            "wr": 0.0,
            "pf": 0.0,
            "net_r": 0.0,
            "dd_r": 0.0,
            "dd_pct": 0.0,
            "max_sl": 0,
        }

    win_r = sum(
        t[2] for t in trades
        if t[2] > 0
    )

    loss_r = sum(
        t[2] for t in trades
        if t[2] < 0
    )

    net_r = sum(
        t[2] for t in trades
    )

    if loss_r < 0:
        pf = win_r / abs(loss_r)
    else:
        pf = 999.0

    wr = (
        wins /
        resolved_count *
        100.0
    )

    # --------------------------------------------------------
    # Equity DD
    # --------------------------------------------------------

    trades.sort(key=lambda x: x[1])

    equity = 0.0
    peak = 0.0
    max_dd = 0.0

    current_sl = 0
    max_sl = 0

    for trade in trades:

        r = trade[2]

        equity += r

        if equity > peak:
            peak = equity

        dd = peak - equity

        if dd > max_dd:
            max_dd = dd

        if r < 0:

            current_sl += 1

            if current_sl > max_sl:
                max_sl = current_sl

        else:

            current_sl = 0

    dd_pct = (
        max_dd *
        RISK_PERCENT
    )

    return {
        "signals": signals,
        "wins": wins,
        "losses": losses,
        "friday_close": friday_close,
        "wr": wr,
        "pf": pf,
        "net_r": net_r,
        "dd_r": max_dd,
        "dd_pct": dd_pct,
        "max_sl": max_sl,
    }


# ============================================================
# PROGRESS BAR
# ============================================================

def progress(current, total, start_time, label):

    percent = current / total * 100

    elapsed = time.time() - start_time

    speed = (
        current / elapsed
        if elapsed > 0
        else 0
    )

    remaining = (
        total - current
    )

    eta = (
        remaining / speed
        if speed > 0
        else 0
    )

    bar_len = 30

    filled = int(
        bar_len *
        current /
        total
    )

    bar = (
        "#" * filled +
        "-" * (bar_len - filled)
    )

    eta_min = int(eta // 60)
    eta_sec = int(eta % 60)

    print(
        f"{label}: "
        f"[{bar}] "
        f"{percent:6.2f}% | "
        f"{current:,}/{total:,} | "
        f"Speed {speed:.1f}/s | "
        f"ETA {eta_min:02d}:{eta_sec:02d}",
        end="\r",
        flush=True
    )


# ============================================================
# RUN IS OPTIMIZATION
# ============================================================

def run_is(
    bars,
    days,
    configs
):

    print()
    print("=" * 110)
    print("IN-SAMPLE OPTIMIZATION")
    print("=" * 110)

    print(
        f"Period: "
        f"{IS_START} "
        f"to "
        f"{IS_END}"
    )

    print(
        f"\nIS Optimization: "
        f"{len(configs):,} configurations"
    )

    results = []

    start_time = time.time()

    for n, config in enumerate(
        configs,
        1
    ):

        result = backtest(
            bars,
            days,
            config,
            IS_START,
            IS_END
        )

        if result["signals"] >= 30:

            row = {
                "time":
                    f"{config[0]:02d}:{config[1]:02d}",

                "hour": config[0],
                "minute": config[1],

                "tp": config[2],
                "sl": config[3],
                "rr": config[4],

                **result
            }

            results.append(row)

        if n % 50 == 0 or n == len(configs):

            progress(
                n,
                len(configs),
                start_time,
                "IS Optimization"
            )

    print()

    # Highest Net R first
    results.sort(
        key=lambda x: (
            x["net_r"],
            x["pf"]
        ),
        reverse=True
    )

    return results


# ============================================================
# RUN OOS
# ============================================================

def run_oos(
    bars,
    days,
    is_results
):

    print()
    print("=" * 110)
    print("WALK-FORWARD OOS TEST")
    print("=" * 110)

    print(
        f"OOS Period: "
        f"{OOS_START} "
        f"to "
        f"{OOS_END}"
    )

    top = is_results[:TOP_N]

    results = []

    for n, is_row in enumerate(
        top,
        1
    ):

        config = (
            is_row["hour"],
            is_row["minute"],
            is_row["tp"],
            is_row["sl"],
            is_row["rr"]
        )

        oos = backtest(
            bars,
            days,
            config,
            OOS_START,
            OOS_END
        )

        row = {

            "time":
                is_row["time"],

            "tp":
                is_row["tp"],

            "sl":
                is_row["sl"],

            "rr":
                is_row["rr"],

            "signals":
                oos["signals"],

            "wr":
                oos["wr"],

            "pf":
                oos["pf"],

            "net_r":
                oos["net_r"],

            "dd_pct":
                oos["dd_pct"],

            "max_sl":
                oos["max_sl"],

            "friday_close":
                oos["friday_close"],

            "is_r":
                is_row["net_r"],

            "is_pf":
                is_row["pf"],

            "is_dd":
                is_row["dd_pct"],

        }

        results.append(row)

    return results


# ============================================================
# PRINT TOP RESULTS
# ============================================================

def print_is_results(results):

    print()
    print("TOP 20 IS RESULTS")

    print(
        " TIME   TP   SL    RR   SIG     WR%      PF      "
        "NetR     DD%  MaxSL"
    )

    print("-" * 90)

    for r in results[:20]:

        print(
            f"{r['time']:>5} "
            f"{r['tp']:>4} "
            f"{r['sl']:>4} "
            f"{r['rr']:>5.2f} "
            f"{r['signals']:>5} "
            f"{r['wr']:>8.2f} "
            f"{r['pf']:>7.3f} "
            f"{r['net_r']:>9.2f} "
            f"{r['dd_pct']:>7.2f} "
            f"{r['max_sl']:>7}"
        )


def print_oos_results(results):

    print()
    print("OOS RESULTS")

    print(
        " TIME   TP   SL    RR   SIG     WR%      PF      "
        "NetR     DD%  MaxSL      IS_R   IS_PF"
    )

    print("-" * 105)

    for r in results:

        print(
            f"{r['time']:>5} "
            f"{r['tp']:>4} "
            f"{r['sl']:>4} "
            f"{r['rr']:>5.2f} "
            f"{r['signals']:>5} "
            f"{r['wr']:>8.2f} "
            f"{r['pf']:>7.3f} "
            f"{r['net_r']:>9.2f} "
            f"{r['dd_pct']:>7.2f} "
            f"{r['max_sl']:>7} "
            f"{r['is_r']:>9.2f} "
            f"{r['is_pf']:>7.3f}"
        )


# ============================================================
# SAVE CSV
# ============================================================

def save_results(
    filename,
    is_results,
    oos_results
):

    with open(
        filename,
        "w",
        newline="",
        encoding="utf-8"
    ) as f:

        writer = csv.writer(f)

        writer.writerow([
            "Stage",
            "Time",
            "TP",
            "SL",
            "RR",
            "Signals",
            "Wins",
            "Losses",
            "FridayClose",
            "WR_%",
            "PF",
            "NetR",
            "DD_%",
            "MaxSL",
            "IS_R",
            "IS_PF",
            "IS_DD_%"
        ])

        for r in is_results[:TOP_N]:

            writer.writerow([
                "IS",
                r["time"],
                r["tp"],
                r["sl"],
                r["rr"],
                r["signals"],
                r["wins"],
                r["losses"],
                r["friday_close"],
                r["wr"],
                r["pf"],
                r["net_r"],
                r["dd_pct"],
                r["max_sl"],
                "",
                "",
                ""
            ])

        for r in oos_results:

            writer.writerow([
                "OOS",
                r["time"],
                r["tp"],
                r["sl"],
                r["rr"],
                r["signals"],
                "",
                "",
                r["friday_close"],
                r["wr"],
                r["pf"],
                r["net_r"],
                r["dd_pct"],
                r["max_sl"],
                r["is_r"],
                r["is_pf"],
                r["is_dd"]
            ])

    print()
    print(f"Saved: {filename}")


# ============================================================
# MAIN
# ============================================================

def main():

    if not os.path.exists(DATA_FILE):

        print(
            f"ERROR: Data file not found:\n"
            f"{DATA_FILE}"
        )

        return

    bars = load_data(DATA_FILE)

    days = build_day_index(bars)

    configs = build_configs()

    print()
    print(
        f"Total configurations: "
        f"{len(configs):,}"
    )

    # --------------------------------------------------------
    # IS
    # --------------------------------------------------------

    is_results = run_is(
        bars,
        days,
        configs
    )

    print()
    print(
        f"Total configurations: "
        f"{len(configs):,}"
    )

    print(
        f"Valid configurations: "
        f"{len(is_results):,}"
    )

    print_is_results(
        is_results
    )

    # --------------------------------------------------------
    # OOS
    # --------------------------------------------------------

    oos_results = run_oos(
        bars,
        days,
        is_results
    )

    print_oos_results(
        oos_results
    )

    # --------------------------------------------------------
    # SAVE
    # --------------------------------------------------------

    save_results(
        OUTPUT_FILE,
        is_results,
        oos_results
    )

    print()
    print("=" * 110)
    print("WALK-FORWARD TEST COMPLETE")
    print("=" * 110)


if __name__ == "__main__":
    main()

