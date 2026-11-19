"""Phil Town's three timing tools: MACD (8,17,9), Stochastics (14,5) and the 10-day moving average."""

from __future__ import annotations

import pandas as pd


def macd(close: pd.Series, fast: int = 8, slow: int = 17, signal: int = 9) -> pd.DataFrame:
    ema_fast = close.ewm(span=fast, adjust=False).mean()
    ema_slow = close.ewm(span=slow, adjust=False).mean()
    line = ema_fast - ema_slow
    sig = line.ewm(span=signal, adjust=False).mean()
    return pd.DataFrame({"macd": line, "signal": sig, "hist": line - sig})


def stochastic(high: pd.Series, low: pd.Series, close: pd.Series,
               k_period: int = 14, d_period: int = 5) -> pd.DataFrame:
    lowest = low.rolling(k_period).min()
    highest = high.rolling(k_period).max()
    k = 100 * (close - lowest) / (highest - lowest)
    d = k.rolling(d_period).mean()
    return pd.DataFrame({"k": k, "d": d})


def tools_signal(hist: pd.DataFrame) -> dict | None:
    """Return the state of the three tools on the last bar.

    hist needs High, Low, Close columns (daily). Each tool is 'buy' or 'sell'.
    Overall is 'buy' if all three say buy, 'sell' if all three say sell, else 'mixed'.
    """
    if hist is None or len(hist) < 30:
        return None
    close = hist["Close"]
    m = macd(close).iloc[-1]
    s = stochastic(hist["High"], hist["Low"], close).iloc[-1]
    ma10 = close.rolling(10).mean().iloc[-1]
    last = close.iloc[-1]
    states = {
        "macd": "buy" if m["macd"] > m["signal"] else "sell",
        "stochastic": "buy" if s["k"] > s["d"] else "sell",
        "ma10": "buy" if last > ma10 else "sell",
    }
    votes = set(states.values())
    overall = votes.pop() if len(votes) == 1 else "mixed"
    return {**states, "overall": overall, "ma10_value": float(ma10)}
