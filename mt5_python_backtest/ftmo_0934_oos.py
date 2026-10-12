from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from collections import defaultdict


# ============================================================
# CONFIG
# ============================================================

DATA_FILE = "FTMO XAUUSD_M1_202208290105_202609302349.csv"

SIGNAL_HOUR = 9
SIGNAL_MINUTE = 34

PIP_SIZE = 0.1

INITIAL_BALANCE = 25000.0
RISK_PERCENT = 0.7
RISK_MONEY = INITIAL_BALANCE * RISK_PERCENT / 100.0

SAME_BAR_RULE = 0

# 2026 OOS
OOS_START = datetime(2026, 1, 1, 0, 0)
OOS_END = datetime(2026, 9, 30, 23, 59)

# The 5 candidates selected from the full optimization
CONFIGS = [
    (67, 93),    # RR 0.72
    (68, 98),    # RR 0.69
    (70, 98),    # RR 0.71
    (97, 93),    # RR 1.04
    (50, 100),   # RR 0.50
]

FRIDAY_CLOSE_HOUR = 23
FRIDAY_CLOSE_MINUTE = 49


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
# LOAD DATA
# ============================================================

def load_bars(filename):

    bars = []

    print("Loading FTMO M1 data...")

    with open(
        filename,
        "r",
        encoding="utf-8",
        errors="ignore"
    ) as f:

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

        if not (
            OOS_START <= bar.dt <= OOS_END
        ):
            continue

        date = bar.dt.date()

        minute = (
            bar.dt.hour * 60
            + bar.dt.minute
        )

        day_index[date][minute] = i

    return day_index


# ============================================================
# EXIT CHECK
# ============================================================

def check_exit(
    bar,
    tp_price,
    sl_price
):

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
# ONE CONFIG
# ============================================================

def run_config(
    bars,
    day_index,
    tp_pips,
    sl_pips,
    friday_close
):

    tp_distance = tp_pips * PIP_SIZE
    sl_distance = sl_pips * PIP_SIZE

    trades = []

    signals = 0
    wins = 0
    losses = 0
    friday_closes = 0

    for date in sorted(day_index.keys()):

        minute_key = (
            SIGNAL_HOUR * 60
            + SIGNAL_MINUTE
        )

        if minute_key not in day_index[date]:
            continue

        signal_idx = day_index[date][minute_key]

        signal_bar = bars[signal_idx]

        signals += 1

        # BUY entry with FTMO spread
        entry_price = (
            signal_bar.close
            + signal_bar.spread * 0.01
        )

        tp_price = (
            entry_price
            + tp_distance
        )

        sl_price = (
            entry_price
            - sl_distance
        )

        entry_dt = signal_bar.dt

        exit_found = False

        # ----------------------------------------------------
        # Search forward
        # ----------------------------------------------------

        for j in range(
            signal_idx + 1,
            len(bars)
        ):

            bar = bars[j]

            if bar.dt <= entry_dt:
                continue

            # ------------------------------------------------
            # Friday forced close
            # ------------------------------------------------

            if (
                friday_close
                and entry_dt.weekday() == 4
                and bar.dt.date() == entry_dt.date()
                and bar.dt.hour == FRIDAY_CLOSE_HOUR
                and bar.dt.minute == FRIDAY_CLOSE_MINUTE
            ):

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

                else:

                    exit_price = bar.close

                    r = (
                        exit_price - entry_price
                    ) / sl_distance

                    if r > 0:
                        wins += 1
                    else:
                        losses += 1

                    friday_closes += 1

                trades.append(
                    (
                        entry_dt,
                        bar.dt,
                        r,
                        exit_type
                        if exit_type is not None
                        else "FRIDAY_CLOSE"
                    )
                )

                exit_found = True
                break

            # ------------------------------------------------
            # Normal TP / SL
            # ------------------------------------------------

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

            trades.append(
                (
                    entry_dt,
                    bar.dt,
                    r,
                    exit_type
                )
            )

            exit_found = True
            break

        # No exit = ignored/pending

    # ========================================================
    # METRICS
    # ========================================================

    if not trades:
        return None

    resolved = trades

    resolved_count = len(resolved)

    # --------------------------------------------------------
    # WR
    # --------------------------------------------------------

    wr = (
        wins / (wins + losses) * 100
        if wins + losses > 0
        else 0
    )

    # --------------------------------------------------------
    # PF
    # --------------------------------------------------------

    gross_profit = sum(
        r for _, _, r, _
        in resolved
        if r > 0
    )

    gross_loss = abs(
        sum(
            r for _, _, r, _
            in resolved
            if r < 0
        )
    )

    pf = (
        gross_profit / gross_loss
        if gross_loss > 0
        else float("inf")
    )

    # --------------------------------------------------------
    # Net R
    # --------------------------------------------------------

    net_r = sum(
        r for _, _, r, _
        in resolved
    )

    # --------------------------------------------------------
    # DD
    # --------------------------------------------------------

    equity = INITIAL_BALANCE
    peak = equity
    max_dd = 0.0

    max_sl_streak = 0
    current_sl_streak = 0

    for (
        entry_dt,
        exit_dt,
        r,
        exit_type
    ) in sorted(
        resolved,
        key=lambda x: x[1]
    ):

        equity += r * RISK_MONEY

        if equity > peak:
            peak = equity

        dd = peak - equity

        if dd > max_dd:
            max_dd = dd

        if r < 0:

            current_sl_streak += 1

            max_sl_streak = max(
                max_sl_streak,
                current_sl_streak
            )

        else:

            current_sl_streak = 0

    dd_percent = (
        max_dd
        / INITIAL_BALANCE
        * 100
    )

    # --------------------------------------------------------
    # Holding time
    # --------------------------------------------------------

    holding_days = [
        (
            exit_dt - entry_dt
        ).total_seconds() / 86400.0
        for (
            entry_dt,
            exit_dt,
            r,
            exit_type
        ) in resolved
    ]

    avg_days = (
        sum(holding_days)
        / len(holding_days)
    )

    max_days = max(holding_days)

    return {
        "TP": tp_pips,
        "SL": sl_pips,
        "RR": tp_pips / sl_pips,

        "Signals": signals,
        "Resolved": resolved_count,

        "Wins": wins,
        "Losses": losses,
        "FridayClose": friday_closes,

        "WR": wr,
        "PF": pf,
        "NetR": net_r,
        "DD": dd_percent,

        "MaxSL": max_sl_streak,

        "AvgDays": avg_days,
        "MaxDays": max_days,
    }


# ============================================================
# MAIN
# ============================================================

def main():

    print("=" * 75)
    print("FTMO 09:34 FIXED — 2026 OOS TEST")
    print("=" * 75)

    print()
    print("OOS:", OOS_START, "to", OOS_END)
    print("Signal: 09:34 BUY")
    print("Configs:", len(CONFIGS))
    print()

    bars = load_bars(DATA_FILE)

    if not bars:
        print("ERROR: No data loaded.")
        return

    print()
    print("Building day index...")

    day_index = build_day_index(bars)

    print(
        f"OOS trading days: {len(day_index)}"
    )

    all_results = []

    for mode_name, friday_close in [
        ("FRIDAY_CLOSE", True),
        ("NO_FORCE_CLOSE", False),
    ]:

        print()
        print("=" * 75)
        print(mode_name)
        print("=" * 75)

        for tp, sl in CONFIGS:

            result = run_config(
                bars,
                day_index,
                tp,
                sl,
                friday_close
            )

            result["Mode"] = mode_name

            all_results.append(result)

            print(
                f"09:34 "
                f"TP={tp:3d} "
                f"SL={sl:3d} "
                f"RR={result['RR']:.2f} "
                f"SIG={result['Signals']:3d} "
                f"WR={result['WR']:.2f}% "
                f"PF={result['PF']:.3f} "
                f"NetR={result['NetR']:.2f} "
                f"DD={result['DD']:.2f}% "
                f"MaxSL={result['MaxSL']} "
                f"AvgD={result['AvgDays']:.3f} "
                f"MaxD={result['MaxDays']:.2f} "
                f"Fri={result['FridayClose']}"
            )

    # ========================================================
    # FINAL COMPARISON
    # ========================================================

    print()
    print("=" * 75)
    print("FINAL OOS COMPARISON")
    print("=" * 75)

    print(
        f"{'Mode':<18}"
        f"{'TP':>5}"
        f"{'SL':>5}"
        f"{'RR':>7}"
        f"{'WR%':>8}"
        f"{'PF':>8}"
        f"{'NetR':>10}"
        f"{'DD%':>9}"
        f"{'MaxSL':>7}"
    )

    for r in all_results:

        print(
            f"{r['Mode']:<18}"
            f"{r['TP']:>5}"
            f"{r['SL']:>5}"
            f"{r['RR']:>7.2f}"
            f"{r['WR']:>8.2f}"
            f"{r['PF']:>8.3f}"
            f"{r['NetR']:>10.2f}"
            f"{r['DD']:>9.2f}"
            f"{r['MaxSL']:>7}"
        )

    print()
    print("=" * 75)
    print("DONE")
    print("=" * 75)


if __name__ == "__main__":
    main()

