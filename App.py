from flask import Flask, request

app = Flask(__name__)

@app.route('/webhook', methods=['POST'])
def webhook():
    data = request.json
    print("🔔 Webhook received:", data)

    signal = data.get("alert", "").upper()

    if signal == "BUY":
        print("✅ BUY signal received!")
        # Add your order logic here
    elif signal == "SELL":
        print("✅ SELL signal received!")
        # Add your order logic here
    else:
        print("⚠️ Unknown signal:", signal)

    return {"status": "ok"}, 200

if __name__ == '__main__':
    app.run(host="0.0.0.0", port=5000)
