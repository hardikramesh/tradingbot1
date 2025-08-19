from flask import Flask, request, jsonify
import os
import sys
import logging
import alpaca_trade_api as tradeapi

app = Flask(__name__)
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")

# --- ENV VARS (set these in Render → Environment) ---
ALPACA_KEY = os.getenv("ALPACA_API_KEY")
ALPACA_SECRET = os.getenv("ALPACA_API_SECRET")
BASE_URL = os.getenv("ALPACA_BASE_URL", "https://paper-api.alpaca.markets")
WEBHOOK_SECRET = os.getenv("WEBHOOK_SECRET")  # create this yourself

if not ALPACA_KEY or not ALPACA_SECRET:
    print("❌ Missing ALPACA_API_KEY or ALPACA_API_SECRET", file=sys.stderr)

api = tradeapi.REST(ALPACA_KEY, ALPACA_SECRET, BASE_URL, api_version="v2")

@app.route("/", methods=["GET"])
def health():
    return jsonify({"status": "ok"}), 200


# --- Helpers ---
def normalize_symbol(sym: str) -> str:
    """Strip vendor prefixes like TVC: and apply simple mappings if needed."""
    s = (sym or "").upper()
    if ":" in s:
        s = s.split(":", 1)[1]
    return s

def current_position_qty(symbol: str) -> int:
    """Return signed integer qty: >0 long, <0 short, 0 no position."""
    try:
        pos = api.get_position(symbol)
        qty = int(float(pos.qty))
        return qty if pos.side == "long" else -qty
    except Exception:
        return 0  # no position

def close_entire_position(symbol: str):
    """Fully flatten symbol position."""
    try:
        api.close_position(symbol)
        logging.info(f"🔚 Closed entire position for {symbol}")
    except Exception as e:
        logging.info(f"ℹ️ No position to close for {symbol}: {e}")


# --- Webhook ---
@app.route("/webhook", methods=["POST"])
def webhook():
    data = request.get_json(force=True, silent=False)
    logging.info(f"📩 Webhook received: {data}")

    # Secret check
    if WEBHOOK_SECRET and data.get("secret") != WEBHOOK_SECRET:
        logging.warning("⛔ Unauthorized webhook (bad secret)")
        return jsonify({"error": "unauthorized"}), 403

    action = str(data.get("alert", "")).upper()
    raw_symbol = str(data.get("symbol", "TSLA"))
    symbol = normalize_symbol(raw_symbol)
    qty = int(data.get("qty", 1))

    # Ensure tradable asset
    try:
        asset = api.get_asset(symbol)
        if not getattr(asset, "tradable", False):
            return jsonify({"error": f"{symbol} not tradable on Alpaca"}), 400
    except Exception as e:
        return jsonify({"error": f"unknown asset {symbol}", "detail": str(e)}), 400

    pos_qty = current_position_qty(symbol)
    logging.info(f"📊 Current position {symbol}: {pos_qty}")

    try:
        if action == "CLOSE":
            close_entire_position(symbol)
            return jsonify({"status": "closed", "symbol": symbol}), 200

        if action == "BUY":
            if pos_qty < 0:  # If short, buy to cover first
                cover_qty = abs(pos_qty)
                logging.info(f"⬆️ Covering {cover_qty} {symbol} before going long")
                api.submit_order(symbol=symbol, qty=cover_qty, side="buy", type="market", time_in_force="gtc")
            api.submit_order(symbol=symbol, qty=qty, side="buy", type="market", time_in_force="gtc")
            logging.info(f"✅ Long opened: +{qty} {symbol}")

        elif action == "SELL":
            if pos_qty > 0:  # If long, close first
                close_qty = pos_qty
                logging.info(f"⬇️ Closing {close_qty} {symbol} before going short")
                api.submit_order(symbol=symbol, qty=close_qty, side="sell", type="market", time_in_force="gtc")
            api.submit_order(symbol=symbol, qty=qty, side="sell", type="market", time_in_force="gtc")
            logging.info(f"✅ Short opened: -{qty} {symbol}")

        else:
            return jsonify({"error": f"unknown action: {action}"}), 400

    except Exception as e:
        logging.error(f"❌ Order error for {symbol}: {e}")
        return jsonify({"error": "order failed", "detail": str(e)}), 500

    return jsonify({"status": "executed", "action": action, "symbol": symbol, "qty": qty}), 200


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)