from flask import Flask, request, jsonify
import alpaca_trade_api as tradeapi
import os, logging

app = Flask(__name__)
logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")

# ── Config (via Render Environment) ───────────────────────────────────────
ALPACA_KEY     = os.getenv("ALPACA_API_KEY")
ALPACA_SECRET  = os.getenv("ALPACA_API_SECRET")
BASE_URL       = os.getenv("ALPACA_BASE_URL", "https://paper-api.alpaca.markets")
WEBHOOK_SECRET = os.getenv("WEBHOOK_SECRET", "")                 # optional
ALLOW_SHORTS   = os.getenv("ALLOW_SHORTS", "true").lower() == "true"

# Cash cap per position (USD). BUY uses fractional notional; SELL uses whole-share qty sized ~ to this cap.
TRADE_NOTIONAL_USD = float(os.getenv("TRADE_NOTIONAL_USD", "100"))

# Alpaca REST client
api = tradeapi.REST(ALPACA_KEY, ALPACA_SECRET, BASE_URL, api_version="v2")

# Remember last action per symbol to ignore duplicates: "BUY" | "SELL" | "FLAT"
last_signal: dict[str, str] = {}

# ── Utility helpers ───────────────────────────────────────────────────────
def get_pos_qty(symbol: str) -> int:
    """+qty for long, -qty for short, 0 if flat/not found."""
    try:
        p = api.get_position(symbol)
        q = int(float(p.qty))
        return q if p.side == "long" else -q
    except Exception:
        return 0

def close_all(symbol: str):
    """Close any open position (long or short)."""
    pos = get_pos_qty(symbol)
    if pos == 0:
        logging.info(f"↪️  {symbol}: already flat, nothing to close.")
        return
    try:
        api.close_position(symbol)
        logging.info(f"✅ Closed all positions for {symbol}")
    except Exception as e:
        logging.error(f"❌ Close error {symbol}: {e}")

def latest_price(symbol: str) -> float | None:
    """Best-effort latest trade price; returns None on failure."""
    try:
        t = api.get_latest_trade(symbol)
        return float(t.price)
    except Exception as e:
        logging.warning(f"⚠️ Latest price unavailable for {symbol}: {e}")
        return None

def place_notional_buy(symbol: str):
    """BUY with fractional notional. Must be DAY TIF."""
    api.submit_order(
        symbol=symbol,
        side="buy",
        type="market",
        time_in_force="day",           # required for fractional/notional
        notional=TRADE_NOTIONAL_USD
    )
    logging.info(f"🧩 BUY {symbol} notional ${TRADE_NOTIONAL_USD} (DAY)")

def place_qty_sell(symbol: str):
    """
    SELL to open short with whole shares (fractional shorting not allowed).
    Sizes qty approximately to the notional cap using latest price; falls back to qty=1.
    """
    px = latest_price(symbol)
    qty = 1
    if px and px > 0:
        est = int(TRADE_NOTIONAL_USD // px)
        qty = max(1, est)
    api.submit_order(
        symbol=symbol,
        side="sell",
        type="market",
        time_in_force="gtc",           # whole shares okay as GTC
        qty=qty
    )
    approx = f"~${qty * (px or 0):.2f}" if px else "~$unknown"
    logging.info(f"🧩 SELL {symbol} qty {qty} ({approx})")

# ── Routes ────────────────────────────────────────────────────────────────
@app.get("/")
def health():
    return jsonify(ok=True), 200

@app.post("/webhook")
def webhook():
    # Parse JSON
    data = request.get_json(force=True, silent=False)

    # Optional shared secret
    if WEBHOOK_SECRET and data.get("secret") != WEBHOOK_SECRET:
        masked = str(data.get("secret", ""))[:2] + "****"
        app.logger.warning(f"Unauthorized webhook: got secret={masked}")
        return jsonify(error="unauthorized"), 403

    # Inputs
    action = str(data.get("alert", "")).upper()          # BUY | SELL | CLOSE
    symbol = str(data.get("symbol", "TSLA")).upper()
    # price is informational; not required
    _price = data.get("price")

    # Validate tradable asset
    try:
        asset = api.get_asset(symbol)
        if not asset.tradable:
            return jsonify(error=f"{symbol} not tradable"), 400
    except Exception as e:
        return jsonify(error=f"unknown asset {symbol}", detail=str(e)), 400

    prev = last_signal.get(symbol, "FLAT")
    pos  = get_pos_qty(symbol)
    logging.info(f"📨 {symbol} got {action}; prev={prev}; pos={pos}")

    try:
        # CLOSE always allowed
        if action == "CLOSE":
            close_all(symbol)
            last_signal[symbol] = "FLAT"
            return jsonify(status="closed", symbol=symbol), 200

        # Duplicate signal? ignore
        if action == prev:
            logging.info(f"⏸  {symbol}: same signal ({action}) as previous, ignoring.")
            return jsonify(status="noop_same_signal", symbol=symbol), 200

        # BUY logic
        if action == "BUY":
            # If currently short, flatten first and wait for next BUY to open (avoids wash trade)
            if pos < 0:
                logging.info(f"🔁 {symbol}: short → flatten before long.")
                close_all(symbol)
                last_signal[symbol] = "FLAT"
                return jsonify(status="flattened_wait_reopen", next="BUY", symbol=symbol), 202

            # Flat → open long with notional (fractional)
            place_notional_buy(symbol)
            last_signal[symbol] = "BUY"
            return jsonify(status="opened_long", symbol=symbol), 200

        # SELL logic
        if action == "SELL":
            if not ALLOW_SHORTS:
                logging.info(f"🚫 Shorting disabled; ignoring SELL for {symbol}.")
                return jsonify(status="shorts_disabled", symbol=symbol), 200

            # If currently long, flatten first and wait for next SELL to open (avoids wash trade)
            if pos > 0:
                logging.info(f"🔁 {symbol}: long → flatten before short.")
                close_all(symbol)
                last_signal[symbol] = "FLAT"
                return jsonify(status="flattened_wait_reopen", next="SELL", symbol=symbol), 202

            # Flat → open short using whole-share qty sized to cap
            place_qty_sell(symbol)
            last_signal[symbol] = "SELL"
            return jsonify(status="opened_short", symbol=symbol), 200

        return jsonify(error=f"unknown action {action}"), 400

    except Exception as e:
        # Common errors: insufficient buying power, PDT, etc.
        logging.error(f"❌ Order error for {symbol}: {e}")
        return jsonify(status="error", details=str(e), symbol=symbol), 400

# ── Entrypoint ────────────────────────────────────────────────────────────
if __name__ == "__main__":
    port = int(os.environ.get("PORT", "10000"))
    app.run(host="0.0.0.0", port=port)
