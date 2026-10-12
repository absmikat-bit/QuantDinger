#!/usr/bin/env python3

import csv
import math
import sys
from datetime import datetime
from pathlib import Path

SIGNAL_HOUR = 23
SIGNAL_MINUTE = 48

TP_PIPS = 85.0
SL_PIPS = 50.0
PIP_SIZE = 0.1

# 0 = SL first when TP and SL are both hit in same candle
SAME_BAR_RULE = 0


def parse_dt(date_s, time_s):
    date_s = date_s.strip().replace("/", ".")
    time_s = time_s.strip()
    return datetime.strptime(
        f"{date_s} {time_s}",
        "%Y.%m.%d %H:%M:%S"
    )


def load_bars(path):
    rows = []

    with open(path, "r", encoding="utf-8-sig") as f:
        next(f, None)

        for line in f:
            parts = line.split()

            if len(parts) < 6:
                continue

            try:
                dt = parse_dt(parts[0], parts[1])

                rows.append({
                    "dt": dt,
                    "open": float(parts[2]),
                    "high": float(parts[3]),
                    "low": float(parts[4]),
                    "close": float(parts[5]),
                })

            except Exception:
                continue

    rows.sort(key=lambda b: b["dt"])
    return rows


def hit_result(bar, tp, sl):

    hit_tp = bar["high"] >= tp
    hit_sl = bar["low"] <= sl

    if hit_tp and hit_sl:
        if SAME_BAR_RULE == 0:
            return -1
        return 1

    if hit_tp:
        return 1

    if hit_sl:
        return -1

    return 0


def build_trades(bars):

    by_date = {}

    for i, b in enumerate(bars):
        by_date.setdefault(
            b["dt"].date(),
            []
        ).append(i)

    trades = []

    for day, idxs in sorted(by_date.items()):

        signal_i = None

        for i in idxs:

            dt = bars[i]["dt"]

            if (
                dt.hour == SIGNAL_HOUR
                and dt.minute == SIGNAL_MINUTE
            ):
                signal_i = i
                break

        if signal_i is None:
            continue

        entry = bars[signal_i]["close"]

        tp = entry + TP_PIPS * PIP_SIZE
        sl = entry - SL_PIPS * PIP_SIZE

        # ============================================================
        # BASELINE
        # ============================================================

        baseline_result = 0
        baseline_exit = None
        baseline_exit_dt = None

        for j in range(signal_i + 1, len(bars)):

            r = hit_result(
                bars[j],
                tp,
                sl
            )

            if r:

                baseline_result = r

                baseline_exit = (
                    tp if r == 1 else sl
                )

                baseline_exit_dt = bars[j]["dt"]

                break

        # ============================================================
        # FTMO SESSION-CLOSE MODEL
        #
        # Entry = 23:48
        # Allow TP/SL through 23:49
        # If neither hit -> force close at 23:49 CLOSE
        # ============================================================

        close_result = 0
        close_exit = None
        close_exit_dt = None
        forced = False

        for j in range(signal_i + 1, len(bars)):

            dt = bars[j]["dt"]

            # Do not go into next day
            if dt.date() != day:
                break

            # Only allow up to 23:49
            if (
                dt.hour > 23
                or (
                    dt.hour == 23
                    and dt.minute > 49
                )
            ):
                break

            r = hit_result(
                bars[j],
                tp,
                sl
            )

            if r:

                close_result = r

                close_exit = (
                    tp if r == 1 else sl
                )

                close_exit_dt = bars[j]["dt"]

                break

            # No TP/SL by 23:49 -> forced close
            if (
                dt.hour == 23
                and dt.minute == 49
            ):

                close_result = 2

                close_exit = bars[j]["close"]

                close_exit_dt = bars[j]["dt"]

                forced = True

                break

        trades.append({
            "date": day,

            "entry": entry,
            "tp": tp,
            "sl": sl,

            "baseline_result": baseline_result,
            "baseline_exit": baseline_exit,
            "baseline_exit_dt": baseline_exit_dt,

            "close_result": close_result,
            "close_exit": close_exit,
            "close_exit_dt": close_exit_dt,

            "forced": forced,
        })

    return trades


def forced_r_multiple(t):

    if t["close_exit"] is None:
        return None

    risk_distance = SL_PIPS * PIP_SIZE

    return (
        (t["close_exit"] - t["entry"])
        / risk_distance
    )


def calc_stats(trades, mode):

    rs = []

    wins = 0
    losses = 0
    pending = 0
    forced = 0

    for t in trades:

        if mode == "baseline":

            result = t["baseline_result"]

            if result == 1:

                wins += 1

                rs.append(
                    TP_PIPS / SL_PIPS
                )

            elif result == -1:

                losses += 1

                rs.append(-1.0)

            else:

                pending += 1

        else:

            result = t["close_result"]

            if result == 1:

                wins += 1

                rs.append(
                    TP_PIPS / SL_PIPS
                )

            elif result == -1:

                losses += 1

                rs.append(-1.0)

            elif result == 2:

                forced += 1

                r = forced_r_multiple(t)

                if r is not None:
                    rs.append(r)

            else:

                pending += 1

    net_r = sum(rs)

    gross_profit = sum(
        x for x in rs
        if x > 0
    )

    gross_loss = -sum(
        x for x in rs
        if x < 0
    )

    if gross_loss:
        pf = gross_profit / gross_loss
    else:
        pf = math.inf

    resolved = len(rs)

    if wins + losses:
        wr = (
            wins
            / (wins + losses)
            * 100
        )
    else:
        wr = 0.0

    return {
        "signals": len(trades),
        "wins": wins,
        "losses": losses,
        "forced": forced,
        "pending": pending,
        "resolved": resolved,
        "wr": wr,
        "net_r": net_r,
        "pf": pf,
    }


def main():

    if len(sys.argv) != 2:

        print(
            "Usage:"
        )

        print(
            "python ftmo_2348_close_test.py "
            "<FTMO_XAUUSD_M1.csv>"
        )

        raise SystemExit(2)

    path = Path(sys.argv[1])

    if not path.exists():

        print(
            f"ERROR: file not found: {path}"
        )

        raise SystemExit(1)

    print("Loading FTMO M1 data...")

    bars = load_bars(path)

    if not bars:

        print(
            "ERROR: no M1 bars loaded"
        )

        raise SystemExit(1)

    print(
        f"Loaded bars: {len(bars):,}"
    )

    trades = build_trades(bars)

    base = calc_stats(
        trades,
        "baseline"
    )

    close = calc_stats(
        trades,
        "close"
    )

    forced_rs = []

    for t in trades:

        if t["forced"]:

            r = forced_r_multiple(t)

            if r is not None:
                forced_rs.append(r)

    # ================================================================
    # RESULT
    # ================================================================

    print()
    print("=" * 70)
    print(
        "FTMO 23:48 BUY TP85 / SL50 SESSION-CLOSE TEST"
    )
    print("=" * 70)

    print()
    print(
        f"Entry      : {SIGNAL_HOUR:02d}:{SIGNAL_MINUTE:02d}"
    )

    print(
        f"TP         : {TP_PIPS:g} pips"
    )

    print(
        f"SL         : {SL_PIPS:g} pips"
    )

    print(
        f"Pip Size   : {PIP_SIZE}"
    )

    print(
        "Close Model: 23:49 পর্যন্ত TP/SL, "
        "তারপর 23:49 CLOSE-এ Forced Close"
    )

    print()

    # ================================================================
    # BASELINE
    # ================================================================

    print("-" * 70)
    print("1) ORIGINAL BACKTEST — NO FORCED CLOSE")
    print("-" * 70)

    print(
        f"Signals       : {base['signals']}"
    )

    print(
        f"Wins          : {base['wins']}"
    )

    print(
        f"Losses        : {base['losses']}"
    )

    print(
        f"Pending       : {base['pending']}"
    )

    print(
        f"Win Rate      : {base['wr']:.2f}%"
    )

    print(
        f"Net R         : {base['net_r']:.4f}"
    )

    print(
        f"Profit Factor : {base['pf']:.4f}"
    )

    # ================================================================
    # FTMO CLOSE
    # ================================================================

    print()
    print("-" * 70)
    print("2) FTMO SESSION-CLOSE MODEL")
    print("-" * 70)

    print(
        f"Signals       : {close['signals']}"
    )

    print(
        f"TP Wins       : {close['wins']}"
    )

    print(
        f"SL Losses     : {close['losses']}"
    )

    print(
        f"Forced Close  : {close['forced']}"
    )

    print(
        f"Pending       : {close['pending']}"
    )

    print(
        f"Win Rate      : {close['wr']:.2f}%"
    )

    print(
        f"Net R         : {close['net_r']:.4f}"
    )

    print(
        f"Profit Factor : {close['pf']:.4f}"
    )

    # ================================================================
    # BEFORE CLOSE
    # ================================================================

    print()
    print("-" * 70)
    print("3) WHAT HAPPENED BEFORE FTMO CLOSE?")
    print("-" * 70)

    tp_sl_hits = (
        close["wins"]
        + close["losses"]
    )

    print(
        f"TP/SL hit by 23:49 : {tp_sl_hits}"
    )

    print(
        f"Forced Close       : {close['forced']}"
    )

    if forced_rs:

        print(
            f"Forced Close Avg R : "
            f"{sum(forced_rs)/len(forced_rs):.4f}"
        )

        print(
            f"Forced Close Min R : "
            f"{min(forced_rs):.4f}"
        )

        print(
            f"Forced Close Max R : "
            f"{max(forced_rs):.4f}"
        )

    # ================================================================
    # CHANGE
    # ================================================================

    print()
    print("-" * 70)
    print("4) CHANGE VS ORIGINAL")
    print("-" * 70)

    print(
        f"Net R Change : "
        f"{close['net_r'] - base['net_r']:+.4f} R"
    )

    print(
        f"WR Change    : "
        f"{close['wr'] - base['wr']:+.2f} percentage points"
    )

    print(
        f"PF Change    : "
        f"{close['pf'] - base['pf']:+.4f}"
    )

    # ================================================================
    # AUDIT CSV
    # ================================================================

    out = path.with_name(
        "ftmo_2348_forced_close_trade_details.csv"
    )

    with out.open(
        "w",
        newline="",
        encoding="utf-8"
    ) as f:

        writer = csv.writer(f)

        writer.writerow([
            "Date",
            "Entry",
            "TP",
            "SL",
            "BaselineResult",
            "BaselineExit",
            "BaselineExitTime",
            "FTMOResult",
            "FTMOExit",
            "FTMOExitTime",
            "ForcedCloseR"
        ])

        for t in trades:

            forced_r = ""

            if t["forced"]:
                r = forced_r_multiple(t)

                if r is not None:
                    forced_r = f"{r:.6f}"

            writer.writerow([
                t["date"],
                f"{t['entry']:.5f}",
                f"{t['tp']:.5f}",
                f"{t['sl']:.5f}",

                t["baseline_result"],
                t["baseline_exit"] or "",
                t["baseline_exit_dt"] or "",

                t["close_result"],
                t["close_exit"] or "",
                t["close_exit_dt"] or "",

                forced_r
            ])

    print()
    print(
        f"Trade audit saved to:"
    )

    print(out)


if __name__ == "__main__":
    main()

