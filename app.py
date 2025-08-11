from flask import Flask, request
import alpaca_trade_api as tradeapi
import os

app = Flask(__name__)

# Replace with your real API keys from Alpaca dashboard
ALPACA_KEY = os.getenv("ALPACA_API_KEY")
ALPACA_SECRET = os.getenv("ALPACA_API_SECRET")
BASE_URL = "https://paper-api.alpaca.markets/"  # Use paper trading endpoint

api = tradeapi.REST(ALPACA_KEY, ALPACA_SECRET, BASE_URL, api_version='v2')

@app.route('/webhook', methods=['POST'])
def webhook():
    data = request.json
    action = data.get("alert", "").upper()
    symbol = data.get("symbol", "TSLA")  # default if missing

    if action == "BUY":
        api.submit_order(
            symbol=symbol,
            qty=1,
            side='buy',
            type='market',
            time_in_force='gtc'
        )
        print(f"✅ BUY order placed for {symbol}")
    elif action == "SELL":
        api.submit_order(
            symbol=symbol,
            qty=1,
            side='sell',
            type='market',
            time_in_force='gtc'
        )
        print(f"✅ SELL order placed for {symbol}")
    else:
        print("⚠️ Unknown alert action")

    return {"status": "executed"}, 200


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
