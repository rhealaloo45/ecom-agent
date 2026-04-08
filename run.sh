./run.sh#!/bin/bash

# --- PriceSync Runner ---

echo "🚀 Stopping existing server on port 5001..."
# Kill any process on port 5001
lsof -ti:5001 | xargs kill -9 2>/dev/null

echo "📦 Starting Pricing Agent with python3..."
echo "💡 (Note: using python3 ensures your libraries are found correctly)"
echo "📄 Logs are being saved to app.log"
echo ""

# Start the server and show logs directly while also saving to app.log
python3 app.py 2>&1 | tee app.log
