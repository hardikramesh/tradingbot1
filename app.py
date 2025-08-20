from flask import Flask, request, jsonify
import alpaca_trade_api as tradeapi
import os
import logging

app = Flask(__name__)
logging.basicConfig(level=logging.INFO)

# --- ENV VARS (set these in Render → Environment) ---
ALPACA_KEY = os.getenv("ALPACA_API_KEY")
ALPACA_SECRET = os.getenv("ALPACA_API_SECRET")
BASE_URL = os.getenv("ALPACA_BASE_URL", "https://paper-api.alpaca.markets")
WEBHOOK_SECRET = os.getenv("WEBHOOK_SECRET")  # create this yourself

api = tradeapi.REST(ALPACA_KEY, ALPACA_SECRET, BASE_URL, api_version="v2")

# --- Helper to close all positions in a symbol ---
def close_entire_position(symbol):
    try:
        api.close_position(symbol)
        logging.info(f"✅ Closed all positions for {symbol}")
    except Exception as e:
        logging.warning(f"⚠️ No position to close for {symbol}: {e}")

@app.route("/webhook", methods=["POST"])
def webhook():
    data = request.json
    action = data.get("alert", "").upper()
    symbol = data.get("symbol", "").upper()
    qty = int(data.get("qty", 1))  # default size = 1

    try:
        pos = api.get_position(symbol)
        pos_qty = int(pos.qty)
    except:
        pos_qty = 0  # no open position

    try:
        if action == "CLOSE":
            close_entire_position(symbol)
            return jsonify({"status": "closed", "symbol": symbol}), 200

        elif action == "BUY":
            if pos_qty <= 0:  # flat or short → allow buy
                close_entire_position(symbol)  # safety
                api.submit_order(symbol=symbol, qty=qty, side="buy",
                                 type="market", time_in_force="gtc")
                logging.info(f"✅ BUY order placed for {symbol}")
            else:
                logging.info(f"ℹ️ Already long {symbol}, skipping buy")

        elif action == "SELL":
            if pos_qty >= 0:  # flat or long → allow sell
                close_entire_position(symbol)  # safety
                api.submit_order(symbol=symbol, qty=qty, side="sell",
                                 type="market", time_in_force="gtc")
                logging.info(f"✅ SELL order placed for {symbol}")
            else:
                logging.info(f"ℹ️ Already short {symbol}, skipping sell")

        else:
            logging.warning(f"⚠️ Unknown action: {action}")

    except Exception as e:
        logging.error(f"[ERROR] ❌ Order error for {symbol}: {e}")
        return jsonify({"status": "error", "details": str(e)}), 400

    return jsonify({"status": "executed", "symbol": symbol}), 200


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=10000)