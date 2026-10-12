import csv
from collections import defaultdict
from datetime import datetime

# ============================================================
# SETTINGS
# ============================================================

DATA_FILE = "FTMO XAUUSD_M1_202208290105_202609302349.csv"

SIGNAL_HOUR = 9
SIGNAL_MINUTE = 34

TP_PIPS = 50
SL_PIPS = 100
PIP_SIZE = 0.1

INITIAL_BALANCE = 25000.0
RISK_PERCENT = 0.7
RISK_MONEY = INITIAL_BALANCE * RISK_PERCENT / 100

# 0 = SL first if both TP and SL touched on same candle
SAME_BAR_RULE = 0

START_DATE = datetime(2022, 8, 29)
END_DATE = datetime(2026, 9, 30, 23, 59, 59)

OUTPUT_TRADES = "ftmo_0934_tp50_sl100_trades.csv"
OUTPUT_YEARLY = "ftmo_0934_tp50_sl100_yearly.csv"
OUTPUT_MONTHLY = "ftmo_0934_tp50_sl100_monthly.csv"


# ============================================================
# LOAD DATA
# ============================================================

def load_bars(filename):
    bars = []

    print("Loading:", filename)

    with open(filename, "r", encoding="utf-8", errors="ignore") as f:
        header = f.readline()

        for line in f:
            parts = line.split()

            if len(parts) < 9:
                continue

            try:
                dt = datetime.strptime(
                    parts[0] + " " + parts[1],
                    "%Y.%m.%d %H:%M:%S"
                )

                if dt < START_DATE or dt > END_DATE:
                    continue

                bars.append({
                    "dt": dt,
                    "open": float(parts[2]),
                    "high": float(parts[3]),
                    "low": float(parts[4]),
                    "close": float(parts[5]),
                    "spread": float(parts[8]),
                })

            except (ValueError, IndexError):
                continue

    print("Bars loaded:", len(bars))

    return bars


# ============================================================
# BACKTEST
# ============================================================

def run_backtest(bars):

    # Group bars by date for easy 09:34 signal lookup
    by_date = defaultdict(list)

    for i, bar in enumerate(bars):
        by_date[bar["dt"].date()].append(i)

    trades = []

    for i, bar in enumerate(bars):

        dt = bar["dt"]

        if dt.hour != SIGNAL_HOUR or dt.minute != SIGNAL_MINUTE:
            continue

        entry = bar["close"]

        # FTMO XAUUSD CSV uses 2 decimal price format.
        # Spread is therefore converted from points to price.
        entry += bar["spread"] * 0.01

        tp_price = entry + TP_PIPS * PIP_SIZE
        sl_price = entry - SL_PIPS * PIP_SIZE

        result = None
        exit_price = None
        exit_dt = None

        # Search forward until TP or SL
        for j in range(i + 1, len(bars)):

            future = bars[j]

            # If we reach beyond available data
            if future["dt"] > END_DATE:
                break

            # TP + SL on same candle
            if future["high"] >= tp_price and future["low"] <= sl_price:

                if SAME_BAR_RULE == 0:
                    result = "LOSS"
                    exit_price = sl_price
                else:
                    result = "WIN"
                    exit_price = tp_price

                exit_dt = future["dt"]
                break

            # TP
            if future["high"] >= tp_price:
                result = "WIN"
                exit_price = tp_price
                exit_dt = future["dt"]
                break

            # SL
            if future["low"] <= sl_price:
                result = "LOSS"
                exit_price = sl_price
                exit_dt = future["dt"]
                break

        if result is None:
            trades.append({
                "signal_dt": dt,
                "exit_dt": "",
                "entry": entry,
                "tp": tp_price,
                "sl": sl_price,
                "result": "PENDING",
                "R": 0.0,
                "holding_days": 0.0,
            })
            continue

        if result == "WIN":
            r = TP_PIPS / SL_PIPS
        else:
            r = -1.0

        holding_days = (
            exit_dt - dt
        ).total_seconds() / 86400

        trades.append({
            "signal_dt": dt,
            "exit_dt": exit_dt,
            "entry": entry,
            "tp": tp_price,
            "sl": sl_price,
            "result": result,
            "R": r,
            "holding_days": holding_days,
        })

    return trades


# ============================================================
# STATISTICS
# ============================================================

def calculate_stats(trades):

    resolved = [
        t for t in trades
        if t["result"] in ("WIN", "LOSS")
    ]

    wins = [t for t in resolved if t["result"] == "WIN"]
    losses = [t for t in resolved if t["result"] == "LOSS"]

    signals = len(trades)
    win_count = len(wins)
    loss_count = len(losses)

    win_rate = (
        win_count / len(resolved) * 100
        if resolved else 0
    )

    gross_profit_r = sum(t["R"] for t in wins)
    gross_loss_r = abs(sum(t["R"] for t in losses))

    pf = (
        gross_profit_r / gross_loss_r
        if gross_loss_r > 0 else 0
    )

    net_r = sum(t["R"] for t in resolved)

    # Equity curve
    equity = INITIAL_BALANCE
    peak = INITIAL_BALANCE
    max_dd = 0
    max_dd_r = 0

    max_loss_streak = 0
    current_loss_streak = 0

    for t in sorted(
        resolved,
        key=lambda x: x["exit_dt"]
    ):

        equity += t["R"] * RISK_MONEY

        if equity > peak:
            peak = equity

        dd = peak - equity

        if dd > max_dd:
            max_dd = dd

        if RISK_MONEY > 0:
            max_dd_r = max_dd / RISK_MONEY

        if t["result"] == "LOSS":
            current_loss_streak += 1

            if current_loss_streak > max_loss_streak:
                max_loss_streak = current_loss_streak
        else:
            current_loss_streak = 0

    max_dd_percent = max_dd / INITIAL_BALANCE * 100

    holding = [
        t["holding_days"]
        for t in resolved
    ]

    avg_holding = (
        sum(holding) / len(holding)
        if holding else 0
    )

    max_holding = max(holding) if holding else 0

    return {
        "signals": signals,
        "wins": win_count,
        "losses": loss_count,
        "pending": signals - len(resolved),
        "win_rate": win_rate,
        "pf": pf,
        "net_r": net_r,
        "max_dd_r": max_dd_r,
        "max_dd_dollars": max_dd,
        "max_dd_percent": max_dd_percent,
        "max_loss_streak": max_loss_streak,
        "avg_holding": avg_holding,
        "max_holding": max_holding,
    }


# ============================================================
# YEARLY / MONTHLY ANALYSIS
# ============================================================

def period_stats(trades, period_key):

    groups = defaultdict(list)

    for t in trades:

        if t["result"] not in ("WIN", "LOSS"):
            continue

        dt = t["signal_dt"]

        if period_key == "year":
            key = dt.strftime("%Y")
        else:
            key = dt.strftime("%Y-%m")

        groups[key].append(t)

    rows = []

    for key in sorted(groups):

        group = groups[key]

        wins = sum(
            1 for t in group
            if t["result"] == "WIN"
        )

        losses = sum(
            1 for t in group
            if t["result"] == "LOSS"
        )

        total = wins + losses

        gross_profit = sum(
            t["R"]
            for t in group
            if t["result"] == "WIN"
        )

        gross_loss = abs(sum(
            t["R"]
            for t in group
            if t["result"] == "LOSS"
        ))

        pf = (
            gross_profit / gross_loss
            if gross_loss > 0 else 0
        )

        net_r = sum(t["R"] for t in group)

        rows.append({
            "period": key,
            "trades": total,
            "wins": wins,
            "losses": losses,
            "win_rate": wins / total * 100 if total else 0,
            "pf": pf,
            "net_r": net_r,
            "profit_dollars": net_r * RISK_MONEY,
        })

    return rows


# ============================================================
# SAVE CSV
# ============================================================

def save_trades(trades):

    with open(
        OUTPUT_TRADES,
        "w",
        newline="",
        encoding="utf-8"
    ) as f:

        writer = csv.writer(f)

        writer.writerow([
            "SignalDateTime",
            "ExitDateTime",
            "Entry",
            "TP",
            "SL",
            "Result",
            "R",
            "ProfitDollars",
            "HoldingDays",
        ])

        for t in trades:

            writer.writerow([
                t["signal_dt"],
                t["exit_dt"],
                f'{t["entry"]:.2f}',
                f'{t["tp"]:.2f}',
                f'{t["sl"]:.2f}',
                t["result"],
                f'{t["R"]:.4f}',
                f'{t["R"] * RISK_MONEY:.2f}',
                f'{t["holding_days"]:.4f}',
            ])


def save_period_csv(rows, filename):

    with open(
        filename,
        "w",
        newline="",
        encoding="utf-8"
    ) as f:

        writer = csv.DictWriter(
            f,
            fieldnames=[
                "period",
                "trades",
                "wins",
                "losses",
                "win_rate",
                "pf",
                "net_r",
                "profit_dollars",
            ]
        )

        writer.writeheader()

        for row in rows:
            writer.writerow(row)


# ============================================================
# CONSOLE REPORT
# ============================================================

def print_report(trades):

    stats = calculate_stats(trades)

    print()
    print("=" * 70)
    print("FTMO XAUUSD 09:34 BUY")
    print("TP = 50 pips | SL = 100 pips | RR = 0.50")
    print("=" * 70)

    print(f"Signals           : {stats['signals']}")
    print(f"Wins              : {stats['wins']}")
    print(f"Losses            : {stats['losses']}")
    print(f"Pending           : {stats['pending']}")
    print(f"Win Rate          : {stats['win_rate']:.2f}%")
    print(f"Profit Factor     : {stats['pf']:.4f}")
    print(f"Net R             : {stats['net_r']:.2f} R")
    print(f"Net Profit        : ${stats['net_r'] * RISK_MONEY:,.2f}")
    print(f"Max DD             : {stats['max_dd_r']:.2f} R")
    print(f"Max DD $           : ${stats['max_dd_dollars']:,.2f}")
    print(f"Max DD %           : {stats['max_dd_percent']:.2f}%")
    print(f"Max Loss Streak    : {stats['max_loss_streak']}")
    print(f"Average Hold       : {stats['avg_holding']:.3f} days")
    print(f"Maximum Hold       : {stats['max_holding']:.3f} days")

    print()
    print("YEARLY RESULTS")
    print("-" * 70)

    yearly = period_stats(trades, "year")

    for r in yearly:

        print(
            f"{r['period']} | "
            f"Trades {r['trades']:4d} | "
            f"WR {r['win_rate']:6.2f}% | "
            f"PF {r['pf']:.3f} | "
            f"NetR {r['net_r']:8.2f} | "
            f"${r['profit_dollars']:,.2f}"
        )

    print()
    print("MONTHLY RESULTS")
    print("-" * 70)

    monthly = period_stats(trades, "month")

    for r in monthly:

        print(
            f"{r['period']} | "
            f"{r['trades']:3d} | "
            f"WR {r['win_rate']:6.2f}% | "
            f"PF {r['pf']:.3f} | "
            f"NetR {r['net_r']:7.2f} | "
            f"${r['profit_dollars']:,.2f}"
        )

    print()
    print("=" * 70)


# ============================================================
# MAIN
# ============================================================

def main():

    print()
    print("FTMO 09:34 FULL ANALYSIS")
    print("Fixed Strategy: BUY | TP50 | SL100")
    print(f"Risk per trade: {RISK_PERCENT}% = ${RISK_MONEY:.2f}")

    bars = load_bars(DATA_FILE)

    trades = run_backtest(bars)

    print_report(trades)

    yearly = period_stats(trades, "year")
    monthly = period_stats(trades, "month")

    save_trades(trades)
    save_period_csv(yearly, OUTPUT_YEARLY)
    save_period_csv(monthly, OUTPUT_MONTHLY)

    print()
    print("Files saved:")
    print(" ", OUTPUT_TRADES)
    print(" ", OUTPUT_YEARLY)
    print(" ", OUTPUT_MONTHLY)


if __name__ == "__main__":
    main()

