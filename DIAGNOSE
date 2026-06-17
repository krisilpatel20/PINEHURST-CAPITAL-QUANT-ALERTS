"""
Diagnostic: dump the last bars of one ticker so we can compare the rail and
signal directly against Streamlit. Run this as a one-off (locally or as a
temporary workflow step) to see WHERE the signal differs.

Usage in a GitHub Actions step:
    run: python diagnose.py IREN
"""

import sys
import pandas as pd
import yfinance as yf
from rail_strategy import compute_signal

PERIOD = "5d"
INTERVAL = "15m"


def fetch_closes(ticker):
    df = yf.download(ticker, period=PERIOD, interval=INTERVAL,
                     progress=False, auto_adjust=True, prepost=False, threads=False)
    if df is None or len(df) < 60:
        return None
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = [c[0] if isinstance(c, tuple) else c for c in df.columns]
    close_col = "Close" if "Close" in df.columns else df.columns[-1]
    px = pd.Series(df[close_col]).dropna().astype(float)
    try:
        if px.index.tz is None:
            px.index = px.index.tz_localize("America/New_York", ambiguous="infer",
                                            nonexistent="shift_forward")
        px.index = px.index.tz_convert("America/Chicago").tz_localize(None)
    except Exception:
        pass
    # Drop forming bar
    try:
        now_ct = pd.Timestamp.now(tz="America/Chicago").tz_localize(None)
        if pd.Timestamp(px.index[-1]) + pd.Timedelta(minutes=15) > now_ct and len(px) > 2:
            px = px.iloc[:-1]
    except Exception:
        if len(px) > 2:
            px = px.iloc[:-1]
    return px.dropna()


def main():
    ticker = sys.argv[1] if len(sys.argv) > 1 else "IREN"
    px = fetch_closes(ticker)
    if px is None:
        print(f"{ticker}: not enough data")
        return

    df = compute_signal(px)
    print(f"=== {ticker} — total bars: {len(df)} ===")
    print(f"first bar: {df.index[0]}   last bar: {df.index[-1]}")
    print()
    print("Last 25 bars (time | close | rail | long_state | signal):")
    tail = df.tail(25)
    for ts, row in tail.iterrows():
        flip = ""
        print(f"  {ts:%m-%d %I:%M%p}  close={row['close']:8.2f}  "
              f"rail={row['rail']:8.2f}  state={int(row['long_state'])}  "
              f"signal={int(row['signal'])}")

    # Show every flip in the whole window
    print()
    print("All signal changes in window:")
    sig = df["signal"]
    chg = sig.diff().fillna(0)
    for ts, c in chg[chg != 0].items():
        kind = "BUY " if c > 0 else "SELL"
        print(f"  {kind} {ts:%m-%d %I:%M%p}  @ {df.loc[ts, 'close']:.2f}")
    print()
    print(f"CURRENT position: {int(sig.iloc[-1])}  (1=long, 0=flat)")


if __name__ == "__main__":
    main()
