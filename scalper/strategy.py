"""VWAP + momentum scalping strategy with improved filtering.

Entry (long):  price crosses above VWAP with a volume spike and positive
               short-term momentum + RSI confirmation -> ride the burst.
Entry (short): mirror image below VWAP.
Exit:          fixed take-profit / stop-loss, or time-based exit.

All signal math is pure Python on lists of bars so it is trivially testable.
"""
from dataclasses import dataclass
from enum import Enum


class Side(str, Enum):
    LONG = "long"
    SHORT = "short"


@dataclass
class Signal:
    side: Side
    reason: str
    confidence: float  # 0.0 to 1.0


def compute_vwap(bars) -> float:
    """Session VWAP from a list of bars (each needs .high .low .close .volume)."""
    pv = sum(((b.high + b.low + b.close) / 3) * b.volume for b in bars)
    vol = sum(b.volume for b in bars)
    return pv / vol if vol else 0.0


def compute_rsi(closes: list, period: int = 14) -> float:
    """Compute RSI from close prices."""
    if len(closes) < period + 1:
        return 50.0  # neutral if not enough data
    
    deltas = [closes[i] - closes[i-1] for i in range(1, len(closes))]
    seed = deltas[:period]
    up = sum(d for d in seed if d > 0) / period
    down = -sum(d for d in seed if d < 0) / period
    
    for d in deltas[period:]:
        up = (up * (period - 1) + (d if d > 0 else 0)) / period
        down = (down * (period - 1) + (-d if d < 0 else 0)) / period
    
    rs = up / down if down else (100 if up > 0 else 0)
    return 100 - (100 / (1 + rs))


def avg_volume(bars, n: int) -> float:
    """Average volume over last n bars."""
    tail = bars[-n:] if len(bars) >= n else bars
    vols = [b.volume for b in tail]
    return sum(vols) / len(vols) if vols else 0.0


def compute_macd(closes: list) -> tuple[float, float, float]:
    """Compute MACD line, signal line, and histogram.
    
    Returns: (macd, signal, histogram)
    """
    if len(closes) < 26:
        return 0.0, 0.0, 0.0
    
    # EMA 12
    ema12 = closes[0]
    for c in closes[1:12]:
        ema12 = ema12 * (11/13) + c * (2/13)
    for c in closes[12:]:
        ema12 = ema12 * (11/13) + c * (2/13)
    
    # EMA 26
    ema26 = closes[0]
    for c in closes[1:26]:
        ema26 = ema26 * (25/27) + c * (2/27)
    for c in closes[26:]:
        ema26 = ema26 * (25/27) + c * (2/27)
    
    macd = ema12 - ema26
    
    # Signal EMA 9 of MACD
    signal = macd
    for _ in range(8):
        signal = signal * (8/10) + macd * (2/10)
    
    return macd, signal, macd - signal


def generate_signal(bars, cfg) -> Signal | None:
    """Return a Signal or None. `bars` = today's 1-min bars, oldest first."""
    if len(bars) < cfg.momentum_lookback + 5:
        return None

    vwap = compute_vwap(bars)
    if vwap == 0:
        return None

    last = bars[-1]
    prev = bars[-2]
    
    closes = [b.close for b in bars]
    dist_pct = (last.close - vwap) / vwap * 100

    # Momentum: close higher/lower than N bars ago
    mom = last.close - bars[-1 - cfg.momentum_lookback].close
    mom_pct = (mom / bars[-1 - cfg.momentum_lookback].close) * 100

    # Volume spike vs recent average (exclude current bar)
    baseline = avg_volume(bars[:-1], 20)
    vol_spike_mult = last.volume / baseline if baseline > 0 else 0
    vol_spike = vol_spike_mult >= cfg.volume_spike_mult

    # RSI for overbought/oversold (filter out exhaustion)
    rsi = compute_rsi(closes)
    
    # VWAP crossover
    crossed_up = prev.close <= vwap and last.close > vwap
    crossed_down = prev.close >= vwap and last.close < vwap

    # Build confidence score: 0.0 to 1.0
    # Higher = better signal
    confidence = 0.0

    # Long signal
    if (
        (crossed_up or dist_pct > cfg.vwap_min_distance_pct)
        and mom > 0
        and last.close > vwap
        and rsi < 70  # not overbought
        and vol_spike
    ):
        # Base confidence from momentum strength
        base = min(1.0, abs(mom_pct) / 0.5)  # 0.5% momentum = 1.0 confidence
        # Boost for crossing
        if crossed_up:
            base = min(1.0, base + 0.2)
        # Volume boost
        vol_boost = min(0.3, (vol_spike_mult - cfg.volume_spike_mult) / 2)
        confidence = min(1.0, base + vol_boost)
        
        if confidence > 0.5:  # Only signal if confidence > 50%
            return Signal(
                Side.LONG,
                f"VWAP↑ cross={crossed_up} mom={mom_pct:+.3f}% vol={vol_spike_mult:.1f}x rsi={rsi:.0f}",
                confidence,
            )

    # Short signal
    if (
        (crossed_down or dist_pct < -cfg.vwap_min_distance_pct)
        and mom < 0
        and last.close < vwap
        and rsi > 30  # not oversold
        and vol_spike
    ):
        # Base confidence from momentum strength
        base = min(1.0, abs(mom_pct) / 0.5)
        # Boost for crossing
        if crossed_down:
            base = min(1.0, base + 0.2)
        # Volume boost
        vol_boost = min(0.3, (vol_spike_mult - cfg.volume_spike_mult) / 2)
        confidence = min(1.0, base + vol_boost)
        
        if confidence > 0.5:
            return Signal(
                Side.SHORT,
                f"VWAP↓ cross={crossed_down} mom={mom_pct:+.3f}% vol={vol_spike_mult:.1f}x rsi={rsi:.0f}",
                confidence,
            )
    
    return None

