from flask import Flask, request, jsonify
import alpaca_trade_api as tradeapi
import os, logging, time

app = Flask(__name__)
logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")

# -------- Config (Render → Environment) --------
ALPACA_KEY     = os.getenv("ALPACA_API_KEY")
ALPACA_SECRET  = os.getenv("ALPACA_API_SECRET")
BASE_URL       = os.getenv("ALPACA_BASE_URL", "https://paper-api.alpaca.markets")
WEBHOOK_SECRET = os.getenv("WEBHOOK_SECRET", "")         # optional
ALLOW_SHORTS   = os.getenv("ALLOW_SHORTS", "true").lower() == "true"

# Your cash cap per position (in USD, e.g. 100 = ~$100 per trade). Requires fractional trading.
TRADE_NOTIONAL_USD = float(os.getenv("TRADE_NOTIONAL_USD", "100"))

# ------------------------------------------------
api = tradeapi.REST(ALPACA_KEY, ALPACA_SECRET, BASE_URL, api_version="v2")
last_signal: dict[str, str] = {}  # remembers last action per symbol: "BUY" | "SELL" | "FLAT"

def get_pos_qty(symbol: str) -> int:
    try:
        p = api.get_position(symbol)
        q = int(float(p.qty))
        return q if p.side == "long" else -q
    except Exception:
        return 0

def close_all(symbol: str):
    pos = get_pos_qty(symbol)
    if pos == 0:
        logging.info(f"↪️  {symbol}: already flat, nothing to close.")
        return
    try:
        api.close_position(symbol)
        logging.info(f"✅ Closed all positions for {symbol}")
    except Exception as e:
        logging.error(f"❌ Close error {symbol}: {e}")

def place_notional(symbol: str, side: str):
    # Places a market order using notional dollars (fractional shares)
    api.submit_order(
        symbol=symbol,
        side=side,                 # "buy" or "sell"
        type="market",
        time_in_force="gtc",
        notional=TRADE_NOTIONAL_USD
    )
    logging.info(f"🧩 {side.upper()} {symbol} notional ${TRADE_NOTIONAL_USD}")

@app.route("/", methods=["GET"])
def health():
    return {"ok": True}, 200

@app.route("/webhook", methods=["POST"])
def webhook():
    data = request.get_json(force=True, silent=False)

    # Optional secret check
    if WEBHOOK_SECRET and data.get("secret") != WEBHOOK_SECRET:
        return jsonify({"error": "unauthorized"}), 403

    action = str(data.get("alert", "")).upper()   # BUY | SELL | CLOSE
    symbol = str(data.get("symbol", "TSLA")).upper()
    price  = float(data.get("price", 0) or 0)     # not required, informational

    # Validate asset tradability
    try:
        asset = api.get_asset(symbol)
        if not asset.tradable:
            return jsonify({"error": f"{symbol} not tradable"}), 400
    except Exception as e:
        return jsonify({"error": f"unknown asset {symbol}", "detail": str(e)}), 400

    prev = last_signal.get(symbol, "FLAT")
    pos  = get_pos_qty(symbol)
    logging.info(f"📨 {symbol} got {action}; prev={prev}; pos={pos}")

    try:
        # 1) CLOSE: always allowed; sets last signal = FLAT
        if action == "CLOSE":
            close_all(symbol)
            last_signal[symbol] = "FLAT"
            return jsonify({"status": "closed", "symbol": symbol}), 200

        # 2) Duplicate signal? Do nothing (keep position)
        if action == prev:
            logging.info(f"⏸  {symbol}: same signal ({action}) as previous, ignoring.")
            return jsonify({"status": "noop_same_signal"}), 200

        # 3) Opposite signal handling: close first (avoid wash trade), then wait for the next alert to open
        if action == "BUY":
            if pos < 0:
                logging.info(f"🔁 {symbol}: short → need to flatten before long.")
                close_all(symbol)
                last_signal[symbol] = "FLAT"
                # Return 202 to indicate we flattened and are waiting for next BUY
                return jsonify({"status": "flattened_wait_reopen", "next": "BUY"}), 202

            # Flat or already long? If flat → open; if already long, this branch won't run due to 'same signal' guard.
            place_notional(symbol, "buy")
            last_signal[symbol] = "BUY"
            return jsonify({"status": "opened_long", "symbol": symbol}), 200

        if action == "SELL":
            if not ALLOW_SHORTS:
                logging.info(f"🚫 Shorting disabled; ignoring SELL for {symbol}.")
                return jsonify({"status": "shorts_disabled"}), 200

            if pos > 0:
                logging.info(f"🔁 {symbol}: long → need to flatten before short.")
                close_all(symbol)
                last_signal[symbol] = "FLAT"
                return jsonify({"status": "flattened_wait_reopen", "next": "SELL"}), 202

            # Flat or already short? If flat → open; if already short, this branch won't run due to 'same signal' guard.
            place_notional(symbol, "sell")
            last_signal[symbol] = "SELL"
            return jsonify({"status": "opened_short", "symbol": symbol}), 200

        return jsonify({"error": f"unknown action {action}"}), 400

    except Exception as e:
        # Common: "insufficient day trading buying power"
        logging.error(f"❌ Order error for {symbol}: {e}")
        return jsonify({"status": "error", "details": str(e)}), 400

if __name__ == "__main__":
    port = int(os.environ.get("PORT", "10000"))
    app.run(host="0.0.0.0", port=port)
