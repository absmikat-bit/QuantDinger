# Standalone MT5 M1 Backtester

This folder contains an isolated, standard-library-only Python backtester for
the Time/TP/SL rules specified for the MT5 EA. It does not import or contact
QuantDinger services, Docker, PostgreSQL, Redis, Kafka, or Celery.

The implementation follows the written EA rules provided with the request.
No MQL5 source file was present in the workspace to compare against.

## Data

`sample_data.csv` is deliberately a header-only template: no market prices
have been fabricated. Export M1 bars from your data source and provide them in
chronological order, with one closed bar per row and these columns:

```csv
datetime,open,high,low,close
```

ISO datetime strings and MT5-style `YYYY.MM.DD HH:MM[:SS]` timestamps are
accepted. Prices must be numeric and internally consistent OHLC values;
timestamps must be strictly increasing. The `symbol` is a configuration
label; the CSV does not need a symbol column. Set `pip_size` for the symbol
and price precision yourself; the engine does not infer it.

## Fixed-configuration backtest

`config.json` contains the requested validation configuration:

- BUY at 09:34
- TP 55 pips, SL 68 pips, pip size 0.1
- Initial balance $25,000 and 0.7% risk
- Same-bar rule 0 (SL first), unlimited hold

The configured weekday filter is Monday-Friday and can be edited in the JSON.
Day names use English weekday names. A signal is created at the matching
candle's close. That candle is excluded from resolution; resolution begins
with the next bar. Signals can coexist if earlier signals have not resolved.
Trades still unresolved at the end of the CSV are counted as pending.

Run from this folder:

```bash
python backtest.py --csv /path/to/your_m1.csv
```

Or choose another JSON configuration:

```bash
python backtest.py --config /path/to/config.json --csv /path/to/your_m1.csv
```

## Optimizer

`optimize.py` reads optimization grids from `config.json`. Ranges are inclusive
`[start, end, step]` triples. The included default is an exhaustive hour,
minute, TP, SL, and BUY/SELL grid; it can be very large and has not been run.
Candidates below `backtest.min_resolved_trades` are excluded.

Ranking is lexicographic:

1. Lowest maximum consecutive SL
2. Highest win rate
3. Highest net R
4. Highest profit factor

Narrow the grid with CLI overrides, for example:

```bash
python optimize.py --csv /path/to/your_m1.csv \
  --hours 18:18 --minutes 10:10 --tp 50:60:5 --sl 60:70:5 \
  --directions BUY,SELL --top 10
```

Ranges use `START:END[:STEP]`; CLI range values are integers. You may edit the
JSON grids for other increments. The optimizer has not been run as part of the
initial implementation.

## Results and drawdown conventions

Signals and consecutive-SL streaks are evaluated in signal-time order.
`Max SL Calendar Days` is the inclusive calendar-day span from the first to
last signal in the longest consecutive-SL streak (one SL is one calendar day).
Closed-trade equity is applied in exit-time order; trades exiting on the same
bar are ordered by signal time. Pending trades do not change equity.
Drawdown is peak-to-trough closed-trade equity only, with no floating
intrabar drawdown. Risk dollars are fixed from initial balance as requested.
Profit factor is `N/A` when no trades resolve, and `inf` when resolved trades
include wins but no losses.

## Tests

The tests use small hand-authored fixtures to check engine behavior; these are
not market data.

```bash
python -m unittest -v
```

`requirements.txt` is included for tooling compatibility; the engine itself
uses only the Python standard library.
