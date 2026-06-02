#!/bin/bash
# Double-click this file in Finder to launch the interactive web app.

set -e
cd "$(dirname "$0")"

clear
echo "================================================"
echo "   Stock Valuation Workbench"
echo "================================================"
echo ""

PY=""
for candidate in python3 python3.12 python3.11 python3.10 /usr/bin/python3 /usr/local/bin/python3 /opt/homebrew/bin/python3; do
    if command -v "$candidate" >/dev/null 2>&1; then
        PY="$candidate"
        break
    fi
done

if [ -z "$PY" ]; then
    echo "ERROR: Python 3 not found on your Mac."
    echo "Install it from: https://www.python.org/downloads/"
    echo ""
    read -p "Press Enter to close..."
    exit 1
fi

echo "Using Python: $PY"
echo ""

"$PY" -c "import yfinance, pandas, numpy, reportlab, anthropic" >/dev/null 2>&1 || {
    echo "First-run setup: installing libraries (yfinance, pandas, numpy, reportlab, anthropic)..."
    "$PY" -m pip install --user --quiet --upgrade yfinance pandas numpy reportlab anthropic || {
        echo ""
        echo "Setup failed. Try this in Terminal:"
        echo "$PY -m pip install --user yfinance pandas numpy reportlab anthropic"
        echo ""
        read -p "Press Enter to close..."
        exit 1
    }
}

# Free port 8765 if a previous instance is still running.
# This prevents "Address already in use" errors when you re-launch.
EXISTING_PID=$(lsof -ti tcp:8765 2>/dev/null)
if [ -n "$EXISTING_PID" ]; then
    echo "Stopping previous server instance (PID $EXISTING_PID)..."
    kill -9 $EXISTING_PID 2>/dev/null
    sleep 1
fi

URL="http://127.0.0.1:8765/stock_evaluator.html"
echo "Opening: $URL"
open "$URL"
echo ""
echo "Keep this window open while using the app."
echo "Press Ctrl+C to stop the local server."
echo ""

"$PY" app_server.py
