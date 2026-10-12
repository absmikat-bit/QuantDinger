import csv
from collections import defaultdict
from datetime import datetime

# ============================================================
# FTMO 09:34 STABILITY OPTIMIZER
# Fixed BUY entry at 09:34
# TP 50-100
# SL 50-100
# Step = 1 pip
# ============================================================

DATA_FILE = "FTMO XAUUSD_M1_202208290105_202609302349.csv"

SIGNAL_HOUR = 9
SIGNAL_MINUTE = 34

PIP_SIZE = 0.1

INITIAL_BALANCE = 25000.0
RISK_PERCENT = 0.7
RISK_MONEY = INITIAL_BALANCE * RISK_PERCENT / 100.0

START_DATE = datetime(2022, 8, 29)
END_DATE = datetime(2026, 9, 30, 23, 59, 59)

TP_START = 50
TP_END = 100

SL_START = 50
SL_END = 100

OUTPUT_FILE = "ftmo_0934_stability_results.csv"


# ============================================================
# LOAD DATA
# ============================================================

def load_bars(filename):

    bars = []

    print("Loading:", filename)

    with open(
        filename,
        "r",
        encoding="utf-8",
        errors="ignore"
    ) as f:

        # Skip header
        f.readline()

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
# GET 09:34 SIGNALS
# ============================================================

def get_signals(bars):

    signals = []

    for i, bar in enumerate(bars):

        dt = bar["dt"]

        if (
            dt.hour == SIGNAL_HOUR
            and dt.minute == SIGNAL_MINUTE
        ):

            # FTMO XAUUSD CSV is 2-digit.
            # Spread points -> price distance.
            entry = (
                bar["close"]
                + bar["spread"] * 0.01
            )

            signals.append({
                "index": i,
                "dt": dt,
                "entry": entry
            })

    return signals


# ============================================================
# TEST ONE TP / SL
# ============================================================

def test_config(
    bars,
    signals,
    tp_pips,
    sl_pips
):

    tp_distance = tp_pips * PIP_SIZE
    sl_distance = sl_pips * PIP_SIZE

    rr = tp_pips / sl_pips

    trades = []

    for signal in signals:

        i = signal["index"]

        entry = signal["entry"]

        tp_price = entry + tp_distance
        sl_price = entry - sl_distance

        result_r = None
        exit_dt = None

        # Search forward until TP or SL
        for j in range(i + 1, len(bars)):

            b = bars[j]

            # Both TP and SL touched
            # Same-bar rule = SL first
            if (
                b["high"] >= tp_price
                and b["low"] <= sl_price
            ):

                result_r = -1.0
                exit_dt = b["dt"]
                break

            # TP
            if b["high"] >= tp_price:

                result_r = rr
                exit_dt = b["dt"]
                break

            # SL
            if b["low"] <= sl_price:

                result_r = -1.0
                exit_dt = b["dt"]
                break

        if result_r is not None:

            trades.append({
                "signal_dt": signal["dt"],
                "exit_dt": exit_dt,
                "R": result_r
            })

    return trades


# ============================================================
# CALCULATE STATISTICS
# ============================================================

def calculate_stats(
    trades,
    tp_pips,
    sl_pips
):

    if not trades:
        return None

    # IMPORTANT:
    # RR must be calculated here because this function
    # receives TP and SL separately.
    rr = tp_pips / sl_pips

    wins = [
        t for t in trades
        if t["R"] > 0
    ]

    losses = [
        t for t in trades
        if t["R"] < 0
    ]

    total = len(trades)

    win_count = len(wins)
    loss_count = len(losses)

    # --------------------------------------------------------
    # WIN RATE
    # --------------------------------------------------------

    win_rate = (
        win_count / total * 100.0
        if total > 0
        else 0.0
    )

    # --------------------------------------------------------
    # PROFIT FACTOR
    # --------------------------------------------------------

    gross_profit = sum(
        t["R"]
        for t in wins
    )

    gross_loss = abs(sum(
        t["R"]
        for t in losses
    ))

    if gross_loss > 0:
        pf = gross_profit / gross_loss
    else:
        pf = 999.0

    # --------------------------------------------------------
    # NET R
    # --------------------------------------------------------

    net_r = sum(
        t["R"]
        for t in trades
    )

    # --------------------------------------------------------
    # EQUITY / MAX DD
    # --------------------------------------------------------

    equity = INITIAL_BALANCE
    peak = INITIAL_BALANCE

    max_dd = 0.0

    # --------------------------------------------------------
    # LOSS STREAK
    # --------------------------------------------------------

    current_loss_streak = 0
    max_loss_streak = 0

    ordered_trades = sorted(
        trades,
        key=lambda x: x["exit_dt"]
    )

    for t in ordered_trades:

        equity += (
            t["R"] * RISK_MONEY
        )

        if equity > peak:
            peak = equity

        dd = peak - equity

        if dd > max_dd:
            max_dd = dd

        if t["R"] < 0:

            current_loss_streak += 1

            if current_loss_streak > max_loss_streak:
                max_loss_streak = (
                    current_loss_streak
                )

        else:

            current_loss_streak = 0

    max_dd_percent = (
        max_dd / INITIAL_BALANCE * 100.0
    )

    # --------------------------------------------------------
    # YEARLY
    # --------------------------------------------------------

    yearly_r = defaultdict(float)

    yearly_wins = defaultdict(int)
    yearly_losses = defaultdict(int)

    for t in trades:

        year = t["signal_dt"].year

        yearly_r[year] += t["R"]

        if t["R"] > 0:
            yearly_wins[year] += 1
        else:
            yearly_losses[year] += 1

    years = sorted(yearly_r.keys())

    positive_years = sum(
        1
        for y in years
        if yearly_r[y] > 0
    )

    negative_years = sum(
        1
        for y in years
        if yearly_r[y] < 0
    )

    worst_year = min(
        yearly_r.values()
    )

    best_year = max(
        yearly_r.values()
    )

    # --------------------------------------------------------
    # MONTHLY
    # --------------------------------------------------------

    monthly_r = defaultdict(float)

    for t in trades:

        month = t["signal_dt"].strftime(
            "%Y-%m"
        )

        monthly_r[month] += t["R"]

    worst_month = min(
        monthly_r.values()
    )

    best_month = max(
        monthly_r.values()
    )

    negative_months = sum(
        1
        for x in monthly_r.values()
        if x < 0
    )

    # --------------------------------------------------------
    # STABILITY SCORE
    # --------------------------------------------------------

    score = 0

    # Profit Factor
    if pf >= 1.20:
        score += 30
    elif pf >= 1.10:
        score += 20
    elif pf >= 1.05:
        score += 10

    # Maximum DD
    if max_dd_percent <= 8:
        score += 25
    elif max_dd_percent <= 10:
        score += 20
    elif max_dd_percent <= 12:
        score += 10

    # Positive years
    score += positive_years * 10

    # Maximum loss streak
    if max_loss_streak <= 6:
        score += 10
    elif max_loss_streak <= 8:
        score += 5

    # Net R
    if net_r >= 50:
        score += 10
    elif net_r >= 25:
        score += 5

    # Penalize negative years
    score -= negative_years * 5

    # Penalize very bad month
    if worst_month < -8:
        score -= 10
    elif worst_month < -5:
        score -= 5

    return {

        "TP": tp_pips,

        "SL": sl_pips,

        "RR": rr,

        "Trades": total,

        "Wins": win_count,

        "Losses": loss_count,

        "WR_%": win_rate,

        "PF": pf,

        "NetR": net_r,

        "DD_%": max_dd_percent,

        "MaxSL": max_loss_streak,

        "PositiveYears": positive_years,

        "NegativeYears": negative_years,

        "WorstYearR": worst_year,

        "BestYearR": best_year,

        "WorstMonthR": worst_month,

        "BestMonthR": best_month,

        "NegativeMonths": negative_months,

        "Score": score
    }


# ============================================================
# MAIN
# ============================================================

def main():

    print()
    print("=" * 70)
    print("FTMO 09:34 STABILITY OPTIMIZER")
    print("BUY | TP 50-100 | SL 50-100")
    print("Fixed Entry Time: 09:34")
    print("Risk per trade: 0.7% = $175")
    print("=" * 70)

    # --------------------------------------------------------
    # LOAD
    # --------------------------------------------------------

    bars = load_bars(DATA_FILE)

    # --------------------------------------------------------
    # SIGNALS
    # --------------------------------------------------------

    signals = get_signals(bars)

    print(
        "09:34 Signals:",
        len(signals)
    )

    # --------------------------------------------------------
    # TOTAL CONFIGURATIONS
    # --------------------------------------------------------

    total_configs = (
        (TP_END - TP_START + 1)
        *
        (SL_END - SL_START + 1)
    )

    print(
        "Total configurations:",
        total_configs
    )

    # --------------------------------------------------------
    # OPTIMIZATION
    # --------------------------------------------------------

    results = []

    counter = 0

    for tp in range(
        TP_START,
        TP_END + 1
    ):

        for sl in range(
            SL_START,
            SL_END + 1
        ):

            counter += 1

            trades = test_config(
                bars,
                signals,
                tp,
                sl
            )

            stats = calculate_stats(
                trades,
                tp,
                sl
            )

            if stats is not None:

                results.append(stats)

            if counter % 100 == 0:

                print(
                    f"Progress: "
                    f"{counter}/{total_configs}"
                )

    # --------------------------------------------------------
    # SORT BY STABILITY SCORE
    # --------------------------------------------------------

    results.sort(
        key=lambda x: x["Score"],
        reverse=True
    )

    # --------------------------------------------------------
    # SAVE ALL RESULTS
    # --------------------------------------------------------

    fields = [
        "TP",
        "SL",
        "RR",
        "Trades",
        "Wins",
        "Losses",
        "WR_%",
        "PF",
        "NetR",
        "DD_%",
        "MaxSL",
        "PositiveYears",
        "NegativeYears",
        "WorstYearR",
        "BestYearR",
        "WorstMonthR",
        "BestMonthR",
        "NegativeMonths",
        "Score"
    ]

    with open(
        OUTPUT_FILE,
        "w",
        newline="",
        encoding="utf-8"
    ) as f:

        writer = csv.DictWriter(
            f,
            fieldnames=fields
        )

        writer.writeheader()

        for row in results:

            writer.writerow(row)

    # ========================================================
    # TOP 30 STABLE
    # ========================================================

    print()
    print("=" * 120)
    print("TOP 30 STABLE CONFIGURATIONS")
    print("=" * 120)

    print(
        f"{'TP':>3} "
        f"{'SL':>3} "
        f"{'RR':>5} "
        f"{'WR%':>7} "
        f"{'PF':>6} "
        f"{'NetR':>8} "
        f"{'DD%':>7} "
        f"{'MaxSL':>6} "
        f"{'PosY':>5} "
        f"{'NegY':>5} "
        f"{'WorstY':>8} "
        f"{'WorstM':>8} "
        f"{'Score':>6}"
    )

    print("-" * 120)

    for r in results[:30]:

        print(
            f"{r['TP']:>3} "
            f"{r['SL']:>3} "
            f"{r['RR']:>5.2f} "
            f"{r['WR_%']:>7.2f} "
            f"{r['PF']:>6.3f} "
            f"{r['NetR']:>8.2f} "
            f"{r['DD_%']:>7.2f} "
            f"{r['MaxSL']:>6} "
            f"{r['PositiveYears']:>5} "
            f"{r['NegativeYears']:>5} "
            f"{r['WorstYearR']:>8.2f} "
            f"{r['WorstMonthR']:>8.2f} "
            f"{r['Score']:>6}"
        )

    # ========================================================
    # PROP FILTER
    # ========================================================

    candidates = [

        r for r in results

        if r["PF"] >= 1.10

        and r["DD_%"] <= 10

        and r["PositiveYears"] >= 4

        and r["MaxSL"] <= 8

        and r["NetR"] > 0

    ]

    candidates.sort(
        key=lambda x: x["Score"],
        reverse=True
    )

    print()
    print("=" * 110)
    print("PROP-FIRM FILTERED CANDIDATES")
    print("=" * 110)

    if not candidates:

        print(
            "NO CONFIGURATION PASSED "
            "ALL PROP-FIRM FILTERS."
        )

    else:

        for r in candidates[:30]:

            print(
                f"TP{r['TP']} / "
                f"SL{r['SL']} | "
                f"RR {r['RR']:.2f} | "
                f"WR {r['WR_%']:.2f}% | "
                f"PF {r['PF']:.3f} | "
                f"NetR {r['NetR']:.2f} | "
                f"DD {r['DD_%']:.2f}% | "
                f"MaxSL {r['MaxSL']} | "
                f"PositiveYears "
                f"{r['PositiveYears']}/5 | "
                f"WorstYear "
                f"{r['WorstYearR']:.2f}R | "
                f"WorstMonth "
                f"{r['WorstMonthR']:.2f}R | "
                f"Score {r['Score']}"
            )

    print()
    print("=" * 70)
    print("DONE")
    print("Saved:", OUTPUT_FILE)
    print("=" * 70)


if __name__ == "__main__":
    main()

