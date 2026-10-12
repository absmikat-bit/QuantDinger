import csv
import math
import time
from collections import defaultdict
from datetime import datetime
from pathlib import Path

# ============================================================
# CONFIGURATION
# ============================================================

CSV_FILE = "FTMO XAUUSD_M1_202208290105_202609302349.csv"

START_MINUTE = 37
END_MINUTE = 48  # 23:49 candle closes at the session boundary

TP_VALUES = range(50, 101, 5)
SL_VALUES = range(30, 101, 5)

MIN_RR = 1.5
MAX_RR = 2.0

PIP_SIZE = 0.1

# FTMO XAUUSD history has two decimal places.
# MT5 SPREAD is expressed in points.
POINT_SIZE = 0.01
USE_ENTRY_SPREAD = True

INITIAL_BALANCE = 25000.0
RISK_PERCENT = 0.7
RISK_MONEY = INITIAL_BALANCE * RISK_PERCENT / 100.0

# If TP and SL are both touched in one candle, assume SL first.
SAME_BAR_SL_FIRST = True

OUTPUT_SUMMARY = "ftmo_swing_optimization_results.csv"
OUTPUT_AUDIT = "ftmo_swing_top_trade_audit.csv"

# ============================================================
# LOAD DATA
# ============================================================

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

                spread_points = (
                    float(p[8])
                    if len(p) > 8
                    else 0.0
                )

                bars.append({
                    "dt": dt,
                    "open": float(p[2]),
                    "high": float(p[3]),
                    "low": float(p[4]),
                    "close": float(p[5]),
                    "spread_points": spread_points,
                })

            except (ValueError, IndexError):
                continue

    bars.sort(key=lambda b: b["dt"])
    return bars


def build_date_ids(bars):
    date_ids = []
    previous_date = None
    current_id = -1

    for bar in bars:
        current_date = bar["dt"].date()

        if current_date != previous_date:
            current_id += 1
            previous_date = current_date

        date_ids.append(current_id)

    return date_ids


# ============================================================
# ONE STRATEGY BACKTEST
# ============================================================

def simulate_strategy(
    bars,
    date_ids,
    signal_indices,
    entry_minute,
    tp_pips,
    sl_pips,
    save_details=False
):
    tp_distance = tp_pips * PIP_SIZE
    sl_distance = sl_pips * PIP_SIZE
    rr = tp_pips / sl_pips

    trades = []

    total_wins = 0
    total_losses = 0
    pending = 0

    gross_profit_r = 0.0
    gross_loss_r = 0.0
    realized_net_r = 0.0
    pending_mtm_r = 0.0

    hold_calendar_days = []
    hold_trading_dates = []

    last_index = len(bars) - 1
    last_bar = bars[last_index]

    for entry_i in signal_indices:
        entry_bar = bars[entry_i]
        entry_dt = entry_bar["dt"]

        # Historical M1 OHLC is treated as bid-side pricing.
        # A BUY enters at Ask = Bid + spread.
        spread_price = (
            entry_bar["spread_points"] * POINT_SIZE
            if USE_ENTRY_SPREAD
            else 0.0
        )

        entry_bid = entry_bar["close"]
        entry_price = entry_bid + spread_price

        target = entry_price + tp_distance
        stop = entry_price - sl_distance

        status = "PENDING"
        exit_i = None
        exit_dt = None
        exit_price = None
        r_multiple = None

        # No daily/weekend forced close.
        # Continue forward until TP or SL is reached.
        for j in range(entry_i + 1, len(bars)):
            bar = bars[j]

            hit_tp = bar["high"] >= target
            hit_sl = bar["low"] <= stop

            if hit_tp and hit_sl:
                if SAME_BAR_SL_FIRST:
                    status = "SL"
                    exit_price = min(stop, bar["open"])
                else:
                    status = "TP"
                    exit_price = max(target, bar["open"])

                exit_i = j
                exit_dt = bar["dt"]
                break

            if hit_sl:
                status = "SL"

                # A gap below the SL may cause worse execution.
                exit_price = min(stop, bar["open"])

                exit_i = j
                exit_dt = bar["dt"]
                break

            if hit_tp:
                status = "TP"

                # A gap above the target may produce a better fill.
                exit_price = max(target, bar["open"])

                exit_i = j
                exit_dt = bar["dt"]
                break

        if exit_i is not None:
            r_multiple = (
                (exit_price - entry_price)
                / sl_distance
            )

            calendar_days = (
                exit_dt.date() - entry_dt.date()
            ).days

            trading_dates = (
                date_ids[exit_i] - date_ids[entry_i] + 1
            )

            hold_calendar_days.append(calendar_days)
            hold_trading_dates.append(trading_dates)

            if r_multiple > 0:
                total_wins += 1
                gross_profit_r += r_multiple
            else:
                total_losses += 1
                gross_loss_r += abs(r_multiple)

            realized_net_r += r_multiple

        else:
            pending += 1

            # Mark unresolved position to the last available close.
            unrealized_r = (
                (last_bar["close"] - entry_price)
                / sl_distance
            )

            pending_mtm_r += unrealized_r

            calendar_days = (
                last_bar["dt"].date() - entry_dt.date()
            ).days

            trading_dates = (
                date_ids[last_index] - date_ids[entry_i] + 1
            )

        trade = {
            "EntryTime": f"23:{entry_minute:02d}",
            "TP_Pips": tp_pips,
            "SL_Pips": sl_pips,
            "RR": round(rr, 4),

            "EntryDate": entry_dt.date().isoformat(),
            "EntryWeekday": entry_dt.strftime("%A"),
            "EntryTimestamp": entry_dt.strftime(
                "%Y-%m-%d %H:%M:%S"
            ),

            "EntryBidClose": round(entry_bid, 5),
            "SpreadPoints": entry_bar["spread_points"],
            "EntrySpreadPrice": round(spread_price, 5),
            "EntryPriceAsk": round(entry_price, 5),

            "TP_Price": round(target, 5),
            "SL_Price": round(stop, 5),

            "Status": status,

            "ExitDate": (
                exit_dt.date().isoformat()
                if exit_dt else ""
            ),
            "ExitWeekday": (
                exit_dt.strftime("%A")
                if exit_dt else ""
            ),
            "ExitTimestamp": (
                exit_dt.strftime("%Y-%m-%d %H:%M:%S")
                if exit_dt else ""
            ),
            "ExitPrice": (
                round(exit_price, 5)
                if exit_price is not None else ""
            ),

            "CalendarDaysHeld": calendar_days,
            "TradingDatesTouched": trading_dates,
            "M1BarsHeld": (
                exit_i - entry_i
                if exit_i is not None else ""
            ),

            "RMultiple": (
                round(r_multiple, 5)
                if r_multiple is not None else ""
            ),
            "UnrealizedRAtDataEnd": (
                round(unrealized_r, 5)
                if exit_i is None else ""
            ),

            # Internal fields used for DD/overlap calculations.
            "_entry_index": entry_i,
            "_exit_index": exit_i,
            "_r": r_multiple,
        }

        trades.append(trade)

    # ========================================================
    # CLOSED-TRADE EQUITY DRAWDOWN
    # ========================================================

    resolved = sorted(
        [t for t in trades if t["_exit_index"] is not None],
        key=lambda t: (t["_exit_index"], t["_entry_index"])
    )

    equity_r = 0.0
    peak_r = 0.0
    max_dd_r = 0.0

    max_loss_streak = 0
    current_loss_streak = 0

    # If multiple positions close on the same M1 bar,
    # apply their combined realized P/L together for equity DD.
    exit_results = defaultdict(list)

    for t in resolved:
        exit_results[t["_exit_index"]].append(t["_r"])

    for exit_i in sorted(exit_results):
        equity_r += sum(exit_results[exit_i])
        peak_r = max(peak_r, equity_r)

        dd_r = peak_r - equity_r
        max_dd_r = max(max_dd_r, dd_r)

    # Consecutive losses are ordered by exit time.
    for t in resolved:
        if t["_r"] < 0:
            current_loss_streak += 1
            max_loss_streak = max(
                max_loss_streak,
                current_loss_streak
            )
        else:
            current_loss_streak = 0

    # ========================================================
    # MAX SIMULTANEOUS OPEN POSITIONS
    # ========================================================

    difference = [0] * (len(bars) + 1)

    for t in trades:
        start_i = t["_entry_index"] + 1

        if start_i >= len(bars):
            continue

        if t["_exit_index"] is None:
            end_i = len(bars) - 1
        else:
            end_i = t["_exit_index"]

        difference[start_i] += 1
        difference[end_i + 1] -= 1

    active = 0
    max_open_positions = 0

    for x in difference[:-1]:
        active += x
        max_open_positions = max(
            max_open_positions,
            active
        )

    # ========================================================
    # SUMMARY
    # ========================================================

    resolved_count = total_wins + total_losses

    win_rate = (
        total_wins / resolved_count * 100
        if resolved_count else 0.0
    )

    profit_factor = (
        gross_profit_r / gross_loss_r
        if gross_loss_r > 0
        else (math.inf if gross_profit_r > 0 else 0.0)
    )

    dd_dollars = max_dd_r * RISK_MONEY

    dd_pct_initial = (
        dd_dollars / INITIAL_BALANCE * 100
    )

    peak_equity_dollars = (
        INITIAL_BALANCE + peak_r * RISK_MONEY
    )

    dd_pct_peak = (
        dd_dollars / peak_equity_dollars * 100
        if peak_equity_dollars > 0 else 0.0
    )

    avg_hold_days = (
        sum(hold_calendar_days) / len(hold_calendar_days)
        if hold_calendar_days else 0.0
    )

    max_hold_days = (
        max(hold_calendar_days)
        if hold_calendar_days else 0
    )

    avg_trading_dates = (
        sum(hold_trading_dates) / len(hold_trading_dates)
        if hold_trading_dates else 0.0
    )

    max_trading_dates = (
        max(hold_trading_dates)
        if hold_trading_dates else 0
    )

    result = {
        "EntryTime": f"23:{entry_minute:02d}",
        "TP_Pips": tp_pips,
        "SL_Pips": sl_pips,
        "RR": round(rr, 4),

        "Signals": len(trades),
        "TP_Wins": total_wins,
        "SL_Losses": total_losses,
        "Pending": pending,

        "WinRatePercent": round(win_rate, 3),
        "ProfitFactor": (
            round(profit_factor, 4)
            if profit_factor != math.inf
            else "Infinity"
        ),

        "NetRealizedR": round(realized_net_r, 4),
        "PendingUnrealizedR": round(pending_mtm_r, 4),
        "NetRPlusPendingMTM": round(
            realized_net_r + pending_mtm_r, 4
        ),

        "MaxClosedDD_R": round(max_dd_r, 4),
        "MaxClosedDD_Dollars": round(dd_dollars, 2),
        "MaxClosedDDPercentInitial": round(
            dd_pct_initial, 4
        ),
        "MaxClosedDDPercentPeakEquity": round(
            dd_pct_peak, 4
        ),

        "MaxConsecutiveLosses": max_loss_streak,

        "AverageCalendarDaysHeld": round(
            avg_hold_days, 3
        ),
        "MaximumCalendarDaysHeld": max_hold_days,

        "AverageTradingDatesTouched": round(
            avg_trading_dates, 3
        ),
        "MaximumTradingDatesTouched": max_trading_dates,

        "MaxSimultaneousOpenPositions": max_open_positions,
        "ResolvedTrades": resolved_count,
    }

    if save_details:
        return result, trades

    return result


# ============================================================
# MAIN OPTIMIZATION
# ============================================================

def main():
    data_path = Path(CSV_FILE)

    if not data_path.exists():
        print(f"ERROR: CSV not found: {data_path}")
        return

    print("Loading FTMO XAUUSD M1 data...")
    bars = load_bars(data_path)

    if not bars:
        print("ERROR: No M1 data loaded.")
        return

    print(f"Loaded bars: {len(bars):,}")
    print(
        f"Data range: {bars[0]['dt']} -> {bars[-1]['dt']}"
    )

    date_ids = build_date_ids(bars)

    signals_by_minute = defaultdict(list)

    for i, bar in enumerate(bars):
        dt = bar["dt"]

        if (
            dt.hour == 23
            and START_MINUTE <= dt.minute <= END_MINUTE
        ):
            signals_by_minute[dt.minute].append(i)

    parameter_pairs = []

    for tp in TP_VALUES:
        for sl in SL_VALUES:
            rr = tp / sl

            if MIN_RR <= rr <= MAX_RR:
                parameter_pairs.append((tp, sl))

    configurations = [
        (minute, tp, sl)
        for minute in range(START_MINUTE, END_MINUTE + 1)
        for tp, sl in parameter_pairs
    ]

    print(f"Entry minutes: 23:{START_MINUTE:02d}-23:{END_MINUTE:02d}")
    print(f"Valid TP/SL pairs: {len(parameter_pairs)}")
    print(f"Total configurations: {len(configurations):,}")
    print(f"Risk per R: ${RISK_MONEY:.2f}")
    print("Forced close: OFF")
    print("Swap/commission: NOT INCLUDED")
    print("DD type: CLOSED-TRADE EQUITY DD")
    print()

    start_time = time.time()
    results = []

    for n, (minute, tp, sl) in enumerate(configurations, 1):
        res = simulate_strategy(
            bars=bars,
            date_ids=date_ids,
            signal_indices=signals_by_minute.get(minute, []),
            entry_minute=minute,
            tp_pips=tp,
            sl_pips=sl,
        )

        results.append(res)

        if n % 25 == 0 or n == len(configurations):
            elapsed = time.time() - start_time

            print(
                f"Progress: {n:,}/{len(configurations):,} "
                f"({100*n/len(configurations):.1f}%) | "
                f"Elapsed: {elapsed:.1f}s"
            )

    # Save every tested configuration.
    summary_path = data_path.parent / OUTPUT_SUMMARY

    columns = list(results[0].keys())

    with summary_path.open(
        "w", newline="", encoding="utf-8-sig"
    ) as f:
        writer = csv.DictWriter(f, fieldnames=columns)
        writer.writeheader()
        writer.writerows(results)

    # Ranking 1: highest realized Net R.
    top_net_r = sorted(
        results,
        key=lambda r: (
            r["NetRealizedR"],
            float(r["ProfitFactor"])
            if r["ProfitFactor"] != "Infinity"
            else 999999,
        ),
        reverse=True
    )

    # Ranking 2: risk-first among positive-net, PF > 1 results.
    candidates = [
        r for r in results
        if r["NetRealizedR"] > 0
        and r["ProfitFactor"] != "Infinity"
        and isinstance(r["ProfitFactor"], (float, int))
        and r["ProfitFactor"] > 1.0
    ]

    if not candidates:
        candidates = results[:]

    risk_first = sorted(
        candidates,
        key=lambda r: (
            r["MaxClosedDDPercentInitial"],
            r["MaxConsecutiveLosses"],
            -(
                float(r["ProfitFactor"])
                if r["ProfitFactor"] != "Infinity"
                else 999999
            ),
            -r["NetRealizedR"],
        )
    )

    print()
    print("=" * 130)
    print("TOP 10 BY REALIZED NET R")
    print("=" * 130)

    header = (
        f"{'Time':<7}{'TP':>5}{'SL':>5}{'RR':>7}"
        f"{'Signals':>9}{'TP':>6}{'SL':>6}{'Open':>7}"
        f"{'WR%':>8}{'PF':>8}{'NetR':>10}"
        f"{'DD%Init':>10}{'MaxSL':>7}"
        f"{'AvgDays':>10}{'MaxDays':>9}{'MaxOpen':>9}"
    )

    print(header)
    print("-" * 130)

    def show_result(r):
        pf = str(r["ProfitFactor"])

        print(
            f"{r['EntryTime']:<7}"
            f"{r['TP_Pips']:>5}"
            f"{r['SL_Pips']:>5}"
            f"{r['RR']:>7.2f}"
            f"{r['Signals']:>9}"
            f"{r['TP_Wins']:>6}"
            f"{r['SL_Losses']:>6}"
            f"{r['Pending']:>7}"
            f"{r['WinRatePercent']:>8.2f}"
            f"{pf:>8}"
            f"{r['NetRealizedR']:>10.2f}"
            f"{r['MaxClosedDDPercentInitial']:>10.2f}"
            f"{r['MaxConsecutiveLosses']:>7}"
            f"{r['AverageCalendarDaysHeld']:>10.2f}"
            f"{r['MaximumCalendarDaysHeld']:>9}"
            f"{r['MaxSimultaneousOpenPositions']:>9}"
        )

    for r in top_net_r[:10]:
        show_result(r)

    print()
    print("=" * 130)
    print("TOP 10 RISK-FIRST CANDIDATES")
    print("=" * 130)
    print(header)
    print("-" * 130)

    for r in risk_first[:10]:
        show_result(r)

    # Generate detailed audit for the top Net R and risk-first candidates.
    audit_keys = set()

    for r in top_net_r[:20] + risk_first[:20]:
        audit_keys.add((
            r["EntryTime"],
            r["TP_Pips"],
            r["SL_Pips"]
        ))

    audit_rows = []

    for key in audit_keys:
        entry_time, tp, sl = key
        minute = int(entry_time.split(":")[1])

        _, trades = simulate_strategy(
            bars=bars,
            date_ids=date_ids,
            signal_indices=signals_by_minute.get(minute, []),
            entry_minute=minute,
            tp_pips=tp,
            sl_pips=sl,
            save_details=True,
        )

        for t in trades:
            row = {
                "EntryTime": t["EntryTime"],
                "TP_Pips": t["TP_Pips"],
                "SL_Pips": t["SL_Pips"],
                "RR": t["RR"],
                "EntryDate": t["EntryDate"],
                "EntryWeekday": t["EntryWeekday"],
                "EntryTimestamp": t["EntryTimestamp"],
                "EntryBidClose": t["EntryBidClose"],
                "SpreadPoints": t["SpreadPoints"],
                "EntrySpreadPrice": t["EntrySpreadPrice"],
                "EntryPriceAsk": t["EntryPriceAsk"],
                "TP_Price": t["TP_Price"],
                "SL_Price": t["SL_Price"],
                "Status": t["Status"],
                "ExitDate": t["ExitDate"],
                "ExitWeekday": t["ExitWeekday"],
                "ExitTimestamp": t["ExitTimestamp"],
                "ExitPrice": t["ExitPrice"],
                "CalendarDaysHeld": t["CalendarDaysHeld"],
                "TradingDatesTouched": t["TradingDatesTouched"],
                "M1BarsHeld": t["M1BarsHeld"],
                "RMultiple": t["RMultiple"],
                "UnrealizedRAtDataEnd": t["UnrealizedRAtDataEnd"],
            }

            audit_rows.append(row)

    audit_path = data_path.parent / OUTPUT_AUDIT

    if audit_rows:
        with audit_path.open(
            "w", newline="", encoding="utf-8-sig"
        ) as f:
            writer = csv.DictWriter(
                f,
                fieldnames=list(audit_rows[0].keys())
            )
            writer.writeheader()
            writer.writerows(audit_rows)

    elapsed = time.time() - start_time

    print()
    print("=" * 80)
    print("OPTIMIZATION COMPLETED")
    print("=" * 80)
    print(f"Configurations tested : {len(results):,}")
    print(f"Elapsed seconds        : {elapsed:.1f}")
    print(f"Full summary CSV       : {summary_path}")
    print(f"Trade-by-trade audit   : {audit_path}")
    print()
    print(
        "IMPORTANT: Swap, commission and full floating-equity DD "
        "are not included yet."
    )
    print(
        "The model permits a new daily entry even when older positions "
        "remain open. Check MaxSimultaneousOpenPositions."
    )


if __name__ == "__main__":
    main()
