import csv
import os
import time
from datetime import datetime, timedelta
from collections import defaultdict


# ============================================================
# SETTINGS
# ============================================================

DATA_FILE = "FTMO XAUUSD_M1_202208290105_202609302349.csv"

INITIAL_BALANCE = 25000.0
RISK_PERCENT = 0.7
PIP_SIZE = 0.1

ENTRY_START = 18 * 60
ENTRY_END = 22 * 60

TP_MIN = 50
TP_MAX = 100
TP_STEP = 5

SL_MIN = 30
SL_MAX = 100
SL_STEP = 5

MIN_RR = 1.5
MAX_RR = 2.0

TOP_N = 20

# Friday 23:49 forced close
FRIDAY_CLOSE = True

IS_START = datetime(2023, 1, 1)
IS_END = datetime(2025, 12, 31, 23, 59)

OOS_START = datetime(2026, 1, 1)
OOS_END = datetime(2026, 9, 30, 23, 59)

OUTPUT_FILE = "wf2_results.csv"


# ============================================================
# BAR
# ============================================================

class Bar:

    __slots__ = (
        "dt",
        "open",
        "high",
        "low",
        "close",
        "spread"
    )

    def __init__(
        self,
        dt,
        op,
        hi,
        lo,
        cl,
        spread
    ):
        self.dt = dt
        self.open = op
        self.high = hi
        self.low = lo
        self.close = cl
        self.spread = spread


# ============================================================
# LOAD FTMO DATA
# ============================================================

def load_data(filename):

    print("Loading FTMO M1 data...")

    bars = []

    with open(
        filename,
        "r",
        encoding="utf-8-sig"
    ) as f:

        for line in f:

            line = line.strip()

            if not line:
                continue

            # Header
            if line.startswith("<DATE>"):
                continue

            # FTMO file = TAB separated
            parts = line.split("\t")

            # Fallback
            if len(parts) < 9:
                parts = line.split()

            if len(parts) < 9:
                continue

            try:

                dt = datetime.strptime(
                    parts[0] + " " + parts[1],
                    "%Y.%m.%d %H:%M"
                )

                op = float(parts[2])
                hi = float(parts[3])
                lo = float(parts[4])
                cl = float(parts[5])

                spread = float(parts[8])

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

    bars.sort(
        key=lambda x: x.dt
    )

    print(
        f"Loaded bars: {len(bars)}"
    )

    if bars:

        print(
            f"First bar: {bars[0].dt}"
        )

        print(
            f"Last bar:  {bars[-1].dt}"
        )

    return bars


# ============================================================
# DAY INDEX
# ============================================================

def build_day_index(bars):

    days = defaultdict(list)

    for i, bar in enumerate(bars):

        days[bar.dt.date()].append(i)

    return days


# ============================================================
# CONFIGURATIONS
# ============================================================

def build_configs():

    configs = []

    for total_minute in range(
        ENTRY_START,
        ENTRY_END + 1
    ):

        hour = total_minute // 60
        minute = total_minute % 60

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

                if (
                    rr < MIN_RR
                    or rr > MAX_RR
                ):
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
# BACKTEST
# ============================================================

def backtest(
    bars,
    days,
    config,
    start_dt,
    end_dt
):

    hour, minute, tp, sl, rr = config

    signals = 0
    wins = 0
    losses = 0
    friday_closes = 0

    trades = []

    current_date = start_dt.date()

    while current_date <= end_dt.date():

        indexes = days.get(
            current_date
        )

        if not indexes:

            current_date += timedelta(days=1)
            continue

        # ----------------------------------------------------
        # Find entry candle
        # ----------------------------------------------------

        entry_idx = None

        for idx in indexes:

            bar = bars[idx]

            if (
                bar.dt.hour == hour
                and bar.dt.minute == minute
            ):

                entry_idx = idx
                break

        if entry_idx is None:

            current_date += timedelta(days=1)
            continue

        entry_bar = bars[entry_idx]

        # BUY entry including spread
        entry_price = (
            entry_bar.close
            + entry_bar.spread * 0.01
        )

        tp_price = (
            entry_price
            + tp * PIP_SIZE
        )

        sl_price = (
            entry_price
            - sl * PIP_SIZE
        )

        signals += 1

        j = entry_idx + 1

        resolved = False

        while j < len(bars):

            bar = bars[j]

            if bar.dt > end_dt:
                break

            # ------------------------------------------------
            # Friday close
            # ------------------------------------------------

            if (
                FRIDAY_CLOSE
                and entry_bar.dt.weekday() == 4
                and bar.dt.date() == entry_bar.dt.date()
                and bar.dt.hour == 23
                and bar.dt.minute == 49
            ):

                hit_sl = (
                    bar.low <= sl_price
                )

                hit_tp = (
                    bar.high >= tp_price
                )

                # Same-bar rule: SL first
                if hit_sl and hit_tp:

                    losses += 1

                    trades.append(
                        (
                            entry_bar.dt,
                            bar.dt,
                            -1.0,
                            "SL"
                        )
                    )

                elif hit_tp:

                    wins += 1

                    trades.append(
                        (
                            entry_bar.dt,
                            bar.dt,
                            rr,
                            "TP"
                        )
                    )

                elif hit_sl:

                    losses += 1

                    trades.append(
                        (
                            entry_bar.dt,
                            bar.dt,
                            -1.0,
                            "SL"
                        )
                    )

                else:

                    # Force close at 23:49 close
                    result_r = (
                        bar.close
                        - entry_price
                    ) / (
                        sl * PIP_SIZE
                    )

                    friday_closes += 1

                    trades.append(
                        (
                            entry_bar.dt,
                            bar.dt,
                            result_r,
                            "FRIDAY_CLOSE"
                        )
                    )

                resolved = True
                break

            # ------------------------------------------------
            # TP / SL
            # ------------------------------------------------

            hit_sl = (
                bar.low <= sl_price
            )

            hit_tp = (
                bar.high >= tp_price
            )

            # SL first
            if hit_sl and hit_tp:

                losses += 1

                trades.append(
                    (
                        entry_bar.dt,
                        bar.dt,
                        -1.0,
                        "SL"
                    )
                )

                resolved = True
                break

            elif hit_tp:

                wins += 1

                trades.append(
                    (
                        entry_bar.dt,
                        bar.dt,
                        rr,
                        "TP"
                    )
                )

                resolved = True
                break

            elif hit_sl:

                losses += 1

                trades.append(
                    (
                        entry_bar.dt,
                        bar.dt,
                        -1.0,
                        "SL"
                    )
                )

                resolved = True
                break

            j += 1

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
            "friday": friday_closes,
            "wr": 0,
            "pf": 0,
            "net_r": 0,
            "dd_pct": 0,
            "max_sl": 0
        }

    gross_profit = sum(
        t[2]
        for t in trades
        if t[2] > 0
    )

    gross_loss = sum(
        t[2]
        for t in trades
        if t[2] < 0
    )

    net_r = sum(
        t[2]
        for t in trades
    )

    if gross_loss < 0:

        pf = (
            gross_profit
            / abs(gross_loss)
        )

    else:

        pf = 999.0

    wr = (
        wins
        / resolved_count
        * 100
    )

    # ========================================================
    # DRAW DOWN
    # ========================================================

    trades.sort(
        key=lambda x: x[1]
    )

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
        max_dd
        * RISK_PERCENT
    )

    return {
        "signals": signals,
        "wins": wins,
        "losses": losses,
        "friday": friday_closes,
        "wr": wr,
        "pf": pf,
        "net_r": net_r,
        "dd_pct": dd_pct,
        "max_sl": max_sl
    }


# ============================================================
# PROGRESS
# ============================================================

def show_progress(
    current,
    total,
    start_time
):

    percent = (
        current
        / total
        * 100
    )

    elapsed = (
        time.time()
        - start_time
    )

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

    length = 30

    filled = int(
        length
        * current
        / total
    )

    bar = (
        "#"
        * filled
        + "-"
        * (length - filled)
    )

    minutes = int(
        eta // 60
    )

    seconds = int(
        eta % 60
    )

    print(
        f"IS Optimization: "
        f"[{bar}] "
        f"{percent:6.2f}% | "
        f"{current:,}/{total:,} | "
        f"Speed {speed:.1f}/s | "
        f"ETA {minutes:02d}:{seconds:02d}",
        end="\r",
        flush=True
    )


# ============================================================
# IS OPTIMIZATION
# ============================================================

def optimize_is(
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

            results.append(
                {
                    "hour": config[0],
                    "minute": config[1],
                    "time":
                        f"{config[0]:02d}:{config[1]:02d}",
                    "tp": config[2],
                    "sl": config[3],
                    "rr": config[4],
                    **result
                }
            )

        if (
            n % 50 == 0
            or n == len(configs)
        ):

            show_progress(
                n,
                len(configs),
                start_time
            )

    print()

    results.sort(
        key=lambda x: (
            x["net_r"],
            x["pf"]
        ),
        reverse=True
    )

    return results


# ============================================================
# PRINT IS
# ============================================================

def print_is(results):

    print()
    print("TOP 20 IS RESULTS")

    print(
        " TIME   TP   SL    RR   SIG     "
        "WR%      PF      NetR     DD%  MaxSL"
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


# ============================================================
# OOS
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

    for r in top:

        config = (
            r["hour"],
            r["minute"],
            r["tp"],
            r["sl"],
            r["rr"]
        )

        oos = backtest(
            bars,
            days,
            config,
            OOS_START,
            OOS_END
        )

        results.append(
            {
                "time": r["time"],
                "tp": r["tp"],
                "sl": r["sl"],
                "rr": r["rr"],
                **oos,
                "is_r": r["net_r"],
                "is_pf": r["pf"],
                "is_dd": r["dd_pct"]
            }
        )

    return results


# ============================================================
# PRINT OOS
# ============================================================

def print_oos(results):

    print()
    print("OOS RESULTS")

    print(
        " TIME   TP   SL    RR   SIG     "
        "WR%      PF      NetR     DD%  MaxSL      IS_R   IS_PF"
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
# SAVE
# ============================================================

def save_csv(
    is_results,
    oos_results
):

    with open(
        OUTPUT_FILE,
        "w",
        newline="",
        encoding="utf-8"
    ) as f:

        writer = csv.writer(f)

        writer.writerow(
            [
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
            ]
        )

        for r in is_results[:TOP_N]:

            writer.writerow(
                [
                    "IS",
                    r["time"],
                    r["tp"],
                    r["sl"],
                    r["rr"],
                    r["signals"],
                    r["wins"],
                    r["losses"],
                    r["friday"],
                    r["wr"],
                    r["pf"],
                    r["net_r"],
                    r["dd_pct"],
                    r["max_sl"],
                    "",
                    "",
                    ""
                ]
            )

        for r in oos_results:

            writer.writerow(
                [
                    "OOS",
                    r["time"],
                    r["tp"],
                    r["sl"],
                    r["rr"],
                    r["signals"],
                    r["wins"],
                    r["losses"],
                    r["friday"],
                    r["wr"],
                    r["pf"],
                    r["net_r"],
                    r["dd_pct"],
                    r["max_sl"],
                    r["is_r"],
                    r["is_pf"],
                    r["is_dd"]
                ]
            )

    print()
    print(
        f"Saved: {OUTPUT_FILE}"
    )


# ============================================================
# MAIN
# ============================================================

def main():

    if not os.path.exists(
        DATA_FILE
    ):

        print(
            "ERROR: Data file not found:"
        )

        print(DATA_FILE)

        return

    bars = load_data(
        DATA_FILE
    )

    if not bars:

        print()
        print(
            "ERROR: 0 bars loaded."
        )

        print(
            "Stopping. No optimization "
            "will be performed."
        )

        return

    days = build_day_index(
        bars
    )

    configs = build_configs()

    print()
    print(
        f"Total configurations: "
        f"{len(configs):,}"
    )

    # --------------------------------------------------------
    # IS
    # --------------------------------------------------------

    is_results = optimize_is(
        bars,
        days,
        configs
    )

    print()
    print(
        f"Valid configurations: "
        f"{len(is_results):,}"
    )

    print_is(
        is_results
    )

    if not is_results:

        print(
            "ERROR: No valid IS results."
        )

        return

    # --------------------------------------------------------
    # OOS
    # --------------------------------------------------------

    oos_results = run_oos(
        bars,
        days,
        is_results
    )

    print_oos(
        oos_results
    )

    # --------------------------------------------------------
    # SAVE
    # --------------------------------------------------------

    save_csv(
        is_results,
        oos_results
    )

    print()
    print("=" * 110)
    print(
        "WALK-FORWARD TEST COMPLETE"
    )
    print("=" * 110)


if __name__ == "__main__":

    main()

