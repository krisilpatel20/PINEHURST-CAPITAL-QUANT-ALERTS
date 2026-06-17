"""
Pinehurst Institutional Trend Rail — the SINGLE source of truth.

This is the exact logic from the Streamlit _kalman_15m_trend_rail_report:
    center      = institutional_adaptive_kalman_trend(close)
    rail, state = institutional_trend_rail(center, atr)
    signal      = (close > rail) AND long_state
    BUY  on signal flip 0 -> 1
    SELL on signal flip 1 -> 0

Nothing else lives here. Both the alert runner and any backtest import THIS file,
so there is only one copy of the math to keep in sync. That is the whole point:
one logic, one place.
"""

import numpy as np
import pandas as pd


# ---- institutional_adaptive_kalman_trend (center line) ----------------------
def adaptive_kalman_center(px, fast_gain=0.34, slow_gain=0.055,
                           vol_window=20, polish_span=3):
    px = pd.Series(px).astype(float).replace([np.inf, -np.inf], np.nan).ffill().bfill()
    if px.empty:
        return pd.Series(dtype=float)

    ret = px.pct_change().abs()
    vol = ret.rolling(vol_window, min_periods=max(3, vol_window // 3)).median().replace(0, np.nan)
    shock = (ret / (vol + 1e-12)).replace([np.inf, -np.inf], np.nan).fillna(0).clip(0, 3) / 3.0

    lo, hi = min(slow_gain, fast_gain), max(slow_gain, fast_gain)
    gains = (slow_gain + (fast_gain - slow_gain) * shock).clip(lo, hi)

    out = np.zeros(len(px), dtype=float)
    out[0] = float(px.iloc[0])
    for i in range(1, len(px)):
        out[i] = out[i - 1] + float(gains.iloc[i]) * (float(px.iloc[i]) - out[i - 1])

    center = pd.Series(out, index=px.index)
    if polish_span > 1:
        center = center.ewm(span=polish_span, adjust=False).mean()
    return center


# ---- institutional_trend_rail (rail + long_state) ---------------------------
def trend_rail(px, fast_gain=0.34, slow_gain=0.055, vol_window=20,
               polish_span=3, atr_window=14, atr_mult=1.35):
    px = pd.Series(px).astype(float).replace([np.inf, -np.inf], np.nan).ffill().bfill()
    if px.empty:
        return pd.Series(dtype=float), pd.Series(dtype=bool)

    center = adaptive_kalman_center(px, fast_gain, slow_gain, vol_window, polish_span)

    atr = px.diff().abs().ewm(span=atr_window, adjust=False).mean().replace(0, np.nan).ffill().bfill()
    atr = atr.fillna(float(px.iloc[-1]) * 0.015)

    slope = center.diff().ewm(span=5, adjust=False).mean().fillna(0)
    long_state = pd.Series(False, index=px.index)
    rail = pd.Series(index=px.index, dtype=float)

    state = True
    rail.iloc[0] = float(center.iloc[0] - float(atr.iloc[0]) * atr_mult)
    long_state.iloc[0] = state

    for i in range(1, len(px)):
        p = float(px.iloc[i]); c = float(center.iloc[i])
        a = float(atr.iloc[i]) * atr_mult; sl = float(slope.iloc[i])
        prev = float(rail.iloc[i - 1]) if np.isfinite(rail.iloc[i - 1]) else c
        if state:
            cand = c - a
            if sl >= 0: cand = max(cand, prev)
            if p < prev: state = False; cand = c + a
        else:
            cand = c + a
            if sl <= 0: cand = min(cand, prev)
            if p > prev: state = True; cand = c - a
        rail.iloc[i] = cand
        long_state.iloc[i] = state

    rail = rail.ewm(span=2, adjust=False).mean()
    return rail, long_state


def compute_signal(close_series):
    """Return a DataFrame with rail, long_state, signal for a close series."""
    px = pd.Series(close_series).astype(float)
    rail, state = trend_rail(px)
    signal = ((px.values > rail.values) & state.values).astype(int)
    return pd.DataFrame({"close": px.values, "rail": rail.values,
                         "long_state": state.values, "signal": signal},
                        index=px.index)


def latest_position(close_series):
    """Return 1 if the rule says we should be LONG on the latest bar, else 0."""
    df = compute_signal(close_series)
    if len(df) == 0:
        return 0
    return int(df["signal"].iloc[-1])
