"""
Pinehurst alert runner.

Runs on a schedule (GitHub Actions). For each ticker:
  1. Download recent 15m bars (yfinance — same vendor Streamlit uses).
  2. Compute the desired position from rail_strategy (the source of truth).
  3. Compare to the position we saved last run (state.json).
  4. If it flipped, send a Telegram BUY/SELL alert.
  5. Save the new position back to state.json (committed by the workflow).

No app needs to be open. No server. The schedule IS the engine.

Environment variables (set as GitHub repo Secrets):
  TELEGRAM_TOKEN  - your bot token from @BotFather
  TELEGRAM_CHAT   - your chat id (from @userinfobot)
  TICKERS         - optional, comma separated, default below
"""

import os
import json
import datetime as dt

import requests
import yfinance as yf

from rail_strategy import compute_signal

STATE_FILE = "state.json"
DEFAULT_TICKERS = "INTC,AAPL,PLTR,RKLB,RDDT"

# Streamlit uses period="5d", interval="15m", regular hours. Match it.
PERIOD = "5d"
INTERVAL = "15m"
TZ = "America/Chicago"


def load_state():
    try:
        with open(STATE_FILE) as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def save_state(state):
    with open(STATE_FILE, "w") as f:
        json.dump(state, f, indent=2)


def send_telegram(token, chat_id, text):
    if not token or not chat_id:
        print("  [no telegram creds — would have sent]:", text)
        return
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    try:
        r = requests.post(url, json={"chat_id": chat_id, "text": text}, timeout=20)
        if r.status_code != 200:
            print("  telegram error:", r.status_code, r.text[:200])
    except Exception as e:
        print("  telegram exception:", e)


def fetch_closes(ticker):
    df = yf.download(ticker, period=PERIOD, interval=INTERVAL,
                     prepost=False, progress=False, auto_adjust=False)
    if df is None or len(df) == 0:
        return None
    close = df["Close"]
    # yfinance can return a single-column frame; squeeze to a Series.
    if hasattr(close, "columns"):
        close = close.iloc[:, 0]
    # Convert index to Chicago time to match the Streamlit log timestamps.
    try:
        close.index = close.index.tz_convert(TZ)
    except (TypeError, AttributeError):
        pass
    return close.dropna()


def main():
    token = os.environ.get("TELEGRAM_TOKEN", "").strip()
    chat_id = os.environ.get("TELEGRAM_CHAT", "").strip()
    tickers = [t.strip().upper() for t in
               os.environ.get("TICKERS", DEFAULT_TICKERS).split(",") if t.strip()]

    state = load_state()
    now = dt.datetime.now(dt.timezone.utc).astimezone()
    print(f"Run at {now:%Y-%m-%d %H:%M %Z} — tickers: {', '.join(tickers)}")

    for ticker in tickers:
        try:
            closes = fetch_closes(ticker)
        except Exception as e:
            print(f"{ticker}: fetch failed — {e}")
            continue

        if closes is None or len(closes) < 60:
            print(f"{ticker}: not enough bars ({0 if closes is None else len(closes)})")
            continue

        df = compute_signal(closes)
        desired = int(df["signal"].iloc[-1])
        price = float(df["close"].iloc[-1])
        rail = float(df["rail"].iloc[-1])
        bar_time = df.index[-1]

        prev = int(state.get(ticker, {}).get("position", 0))

        if desired != prev:
            if desired == 1:
                msg = (f"PINEHURST BUY {ticker}\n"
                       f"Entry: {price:.2f}\n"
                       f"Rail: {rail:.2f}\n"
                       f"Bar: {bar_time:%Y-%m-%d %I:%M %p %Z}")
            else:
                entry = float(state.get(ticker, {}).get("entry_price", price))
                pnl = (price / entry - 1.0) * 100.0 if entry else 0.0
                msg = (f"PINEHURST SELL {ticker}\n"
                       f"Exit: {price:.2f}\n"
                       f"PnL: {pnl:+.2f}%\n"
                       f"Rail: {rail:.2f}\n"
                       f"Bar: {bar_time:%Y-%m-%d %I:%M %p %Z}")
            print(f"{ticker}: FLIP {prev} -> {desired} | {price:.2f}")
            send_telegram(token, chat_id, msg)
        else:
            print(f"{ticker}: no change (pos={desired}, px={price:.2f})")

        entry_price = (price if desired == 1 and prev == 0
                       else state.get(ticker, {}).get("entry_price"))
        state[ticker] = {
            "position": desired,
            "entry_price": entry_price,
            "last_price": price,
            "last_rail": rail,
            "updated": now.isoformat(),
        }

    save_state(state)
    print("State saved.")


if __name__ == "__main__":
    main()
