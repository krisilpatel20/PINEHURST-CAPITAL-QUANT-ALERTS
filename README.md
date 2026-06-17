# Pinehurst Trend Rail — 24/7 Alerts (no app, no server)

This sends you Telegram **BUY/SELL alerts** whenever the Institutional Trend Rail
signal flips, running **every 15 minutes on its own** — even when your computer is
off and no app is open. There is no Streamlit server to keep awake, no
QuantConnect, no hosting bill.

It works because the strategy runs as a **scheduled GitHub Action**, not as an app.
The schedule is the engine.

## How it fits together

- `rail_strategy.py` — the exact rail logic (the one source of truth).
- `run_alerts.py` — fetches 15m data, checks for a flip vs last run, sends Telegram.
- `state.json` — remembers each ticker's position between runs (no database needed).
- `.github/workflows/alerts.yml` — runs the check every 15 min during market hours.
- `.github/workflows/keepalive.yml` — weekly nudge so GitHub never auto-disables it.

## One-time setup (about 10 minutes)

### 1. Make a Telegram bot
1. In Telegram, message **@BotFather**, send `/newbot`, follow prompts.
2. Copy the **bot token** it gives you (looks like `1234567:AAabc...`).
3. Message **@userinfobot** to get your numeric **chat id**.
4. Send your new bot any message once (so it's allowed to message you).

### 2. Create the GitHub repo
1. Make a new repo (a **public** repo = unlimited free minutes; private gets
   ~2000 free min/month, which is plenty for this).
2. Upload all these files, keeping the `.github/workflows/` folder structure.

### 3. Add your secrets
In the repo: **Settings → Secrets and variables → Actions**.
- Under **Secrets**, add:
  - `TELEGRAM_TOKEN` = your bot token
  - `TELEGRAM_CHAT` = your chat id
- Under **Variables** (optional), add:
  - `TICKERS` = e.g. `INTC,AAPL,PLTR,RKLB,RDDT`

### 4. Turn it on
1. Go to the **Actions** tab, enable workflows if prompted.
2. Open **Pinehurst Alerts → Run workflow** to test it once manually.
3. Check the run log — you'll see each ticker's position. If a flip happens
   you'll get a Telegram message.

That's it. From now on it runs itself every 15 minutes, 13:00–21:00 UTC, Mon–Fri.

## Notes / honest limitations

- **Timing:** GitHub scheduled jobs can be delayed 5–20 min at peak load. Alerts
  may arrive a few minutes after the bar closes. Fine for swing/trend signals;
  not for scalping.
- **Market hours:** the schedule covers US regular hours in UTC. During US
  daylight saving the local window shifts by an hour; widen the `13-20` hour
  range in `alerts.yml` if you want a buffer.
- **Data vendor:** uses yfinance, the same source as your Streamlit log, so
  signals line up with what you saw there (closes can differ by cents from
  brokerage feeds, which is normal).
- **Alerts only:** this notifies you; it does not place orders. That keeps it
  simple and broker-agnostic. Placing real orders would add a broker API and is
  a separate step if you ever want it.
- **Changing the strategy:** edit `rail_strategy.py` only. Both alerts and any
  backtest you write import it, so there's never more than one copy to keep
  in sync — which is what was causing the mismatches before.

## Running a backtest with the SAME logic (optional)

Because the logic lives in one importable file, you can reproduce the exact
trade log anytime:

```python
import yfinance as yf
from rail_strategy import compute_signal

closes = yf.download("AAPL", period="5d", interval="15m", prepost=False)["Close"]
df = compute_signal(closes)
flips = df.signal.diff().fillna(0)
print(df[flips != 0])   # every entry/exit, identical to the alerts
```
