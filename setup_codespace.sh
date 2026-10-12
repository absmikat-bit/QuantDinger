#!/usr/bin/env bash
set -e

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BACKTEST_DIR="$PROJECT_ROOT/mt5_python_backtest"
DATA_GZ="$BACKTEST_DIR/FTMO XAUUSD_M1_202208290105_202609302349.csv.gz"
DATA_CSV="$BACKTEST_DIR/FTMO XAUUSD_M1_202208290105_202609302349.csv"

echo "========================================"
echo " QuantDinger Codespace Setup"
echo "========================================"

echo
echo "[1/5] Checking Python..."
python --version
pip --version

echo
echo "[2/5] Checking FTMO backtest directory..."
if [ ! -d "$BACKTEST_DIR" ]; then
    echo "ERROR: $BACKTEST_DIR not found."
    exit 1
fi

echo
echo "[3/5] Checking FTMO dataset..."
if [ -f "$DATA_CSV" ]; then
    echo "Raw FTMO CSV already exists."
elif [ -f "$DATA_GZ" ]; then
    echo "Compressed FTMO CSV found."
    echo "Extracting dataset..."
    gzip -dk "$DATA_GZ"
else
    echo "ERROR: FTMO dataset not found:"
    echo "$DATA_GZ"
    exit 1
fi

echo
echo "[4/5] Checking backtester..."
if [ ! -f "$BACKTEST_DIR/backtest.py" ]; then
    echo "ERROR: backtest.py not found."
    exit 1
fi

if [ ! -f "$BACKTEST_DIR/requirements.txt" ]; then
    echo "ERROR: requirements.txt not found."
    exit 1
fi

echo "Backtester found."
echo "Python dependencies: standard library only."

echo
echo "[5/5] Setup complete."
echo
echo "Project: $PROJECT_ROOT"
echo "Data:    $DATA_CSV"
echo "Engine:  $BACKTEST_DIR/backtest.py"
echo
echo "========================================"
echo " Ready for FTMO research"
echo "========================================"
