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
    """
    EXACT copy of Streamlit's _fetch_15m_completed_bars so the alert signal
    matches the Streamlit trade log. Three things that must match (and were
    wrong before):
      1. auto_adjust=True  (adjusts prices; changes every close and the rail)
      2. drop the currently-forming 15m bar (only act on CLOSED candles)
      3. tz: localize US/Eastern -> convert America/Chicago -> drop tz
    """
    df = yf.download(
        ticker,
        period=PERIOD,
        interval=INTERVAL,
        progress=False,
        auto_adjust=True,
        prepost=False,
        threads=False,
    )
    if df is None or len(df) < 60:
        return None

    if isinstance(df.columns, pd.MultiIndex):
        df.columns = [c[0] if isinstance(c, tuple) else c for c in df.columns]

    close_col = "Close" if "Close" in df.columns else df.columns[-1]
    px = pd.Series(df[close_col]).dropna().astype(float)
    if len(px) < 60:
        return None

    # yfinance intraday is US/Eastern. Convert to CT, then drop tz (as Streamlit).
    try:
        if px.index.tz is None:
            px.index = px.index.tz_localize("America/New_York",
                                            ambiguous="infer",
                                            nonexistent="shift_forward")
        px.index = px.index.tz_convert("America/Chicago").tz_localize(None)
    except Exception:
        pass

    # Keep ONLY regular-session bars: 8:30am–3:00pm CT (= 9:30am–4:00pm ET).
    # yfinance labels bars by START time, so the last regular bar starts 2:45pm
    # CT (closes 3:00pm). This stops any after-hours/overnight print from
    # flipping the signal — the cause of "everything flat at midnight".
    try:
        t = px.index.time
        from datetime import time as _time
        mask = (t >= _time(8, 30)) & (t <= _time(14, 45))
        px = px[mask]
    except Exception:
        pass

    if len(px) < 60:
        return None

    # Drop the latest bar ONLY if that 15m candle is still forming.
    # yfinance labels intraday bars by candle START time; a 2:30 PM CT bar
    # closes at 2:45 PM CT. This is the key fix: never alert off a half-formed
    # candle, exactly like Streamlit.
    try:
        now_ct = pd.Timestamp.now(tz="America/Chicago").tz_localize(None)
        latest_start = pd.Timestamp(px.index[-1])
        latest_close = latest_start + pd.Timedelta(minutes=15)
        if latest_close > now_ct and len(px) > 2:
            px = px.iloc[:-1]
    except Exception:
        if len(px) > 2:
            px = px.iloc[:-1]

    return px.dropna()


def main():
    token = os.environ.get("TELEGRAM_TOKEN", "").strip()
    chat_id = os.environ.get("TELEGRAM_CHAT", "").strip()
    tickers = [t.strip().upper() for t in
               os.environ.get("TICKERS", DEFAULT_TICKERS).split(",") if t.strip()]

    # PRIME mode: record each ticker's TRUE current position WITHOUT sending any
    # alerts. Run this once after changing tickers or resetting state, so the
    # saved memory matches reality. Then turn it off and only real flips alert.
    prime = os.environ.get("PRIME", "false").strip().lower() in ("true", "1", "yes", "y")

    state = load_state()
    now = dt.datetime.now(dt.timezone.utc).astimezone()
    mode = "PRIME (no alerts)" if prime else "live"
    print(f"Run at {now:%Y-%m-%d %H:%M %Z} [{mode}] — tickers: {', '.join(tickers)}")

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

        if prime:
            # Just record the truth, say nothing.
            print(f"{ticker}: primed position={desired} (px={price:.2f}) — no alert")
        elif desired != prev:
            if desired == 1:
                msg = (f"PINEHURST BUY {ticker}\n"
                       f"Entry: {price:.2f}\n"
                       f"Rail: {rail:.2f}\n"
                       f"Bar: {bar_time:%Y-%m-%d %I:%M %p CT}")
            else:
                entry = float(state.get(ticker, {}).get("entry_price", price))
                pnl = (price / entry - 1.0) * 100.0 if entry else 0.0
                msg = (f"PINEHURST SELL {ticker}\n"
                       f"Exit: {price:.2f}\n"
                       f"PnL: {pnl:+.2f}%\n"
                       f"Rail: {rail:.2f}\n"
                       f"Bar: {bar_time:%Y-%m-%d %I:%M %p CT}")
            print(f"{ticker}: FLIP {prev} -> {desired} | {price:.2f}")
            send_telegram(token, chat_id, msg)
        else:
            print(f"{ticker}: no change (pos={desired}, px={price:.2f})")

        # entry_price: set when entering long; keep existing otherwise.
        if desired == 1 and prev == 0:
            entry_price = price
        elif desired == 1:
            entry_price = state.get(ticker, {}).get("entry_price", price)
        else:
            entry_price = None
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
