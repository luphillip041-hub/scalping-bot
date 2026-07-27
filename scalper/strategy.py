"""VWAP + momentum + RSI scalping strategy with multi-timeframe confirmation.

Entry (long):  price crosses above VWAP with volume spike, positive momentum,
               and RSI not overbought -> ride the burst.
Entry (short): mirror image below VWAP with RSI not oversold.
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
    confidence: float  # 0.0-1.0 confidence score for this signal


def compute_vwap(bars) -> float:
    """Session VWAP from a list of bars (each needs .high .low .close .volume).
    
    Returns 0 if no valid bars or zero volume.
    """
    if not bars:
        return 0.0
    
    pv = sum(((b.high + b.low + b.close) / 3) * b.volume for b in bars)
    vol = sum(b.volume for b in bars)
    return pv / vol if vol > 0 else 0.0


def compute_rsi(closes: list, period: int = 14) -> float:
    """Compute RSI for the most recent close.
    
    Args:
        closes: list of closing prices (oldest first)
        period: lookback period (default 14)
    
    Returns:
        RSI value 0-100, or None if insufficient data
    """
    if len(closes) < period + 1:
        return None
    
    tail = closes[-period-1:]
    deltas = [tail[i] - tail[i-1] for i in range(1, len(tail))]
    
    gains = [d for d in deltas if d > 0]
    losses = [abs(d) for d in deltas if d < 0]
    
    avg_gain = sum(gains) / period if gains else 0.0
    avg_loss = sum(losses) / period if losses else 0.0
    
    if avg_loss == 0:
        return 100.0 if avg_gain > 0 else 50.0
    
    rs = avg_gain / avg_loss
    return 100 - (100 / (1 + rs))


def avg_volume(bars, n: int) -> float:
    """Average volume of the last n bars (or fewer if fewer bars exist)."""
    if not bars:
        return 0.0
    tail = bars[-n:] if len(bars) >= n else bars
    vols = [b.volume for b in tail]
    return sum(vols) / len(vols) if vols else 0.0


def compute_atr(bars, period: int = 14) -> float:
    """Average True Range for volatility measurement.
    
    Args:
        bars: list of bars (each needs .high .low .close)
        period: lookback period (default 14)
    
    Returns:
        ATR value, or 0 if insufficient data
    """
    if len(bars) < period + 1:
        return 0.0
    
    tail = bars[-period-1:]
    true_ranges = []
    for i in range(1, len(tail)):
        h = tail[i].high
        l = tail[i].low
        pc = tail[i-1].close
        tr = max(h - l, abs(h - pc), abs(l - pc))
        true_ranges.append(tr)
    
    return sum(true_ranges) / len(true_ranges) if true_ranges else 0.0


def generate_signal(bars, cfg) -> Signal | None:
    """Return a Signal or None. `bars` = today's 1-min bars, oldest first.
    
    Validates sufficient bar count before computing indicators.
    Multi-indicator confirmation: VWAP + Volume + Momentum + RSI
    """
    # Require minimum bars for all indicators
    min_bars = max(cfg.momentum_lookback, 20) + 5
    if len(bars) < min_bars:
        return None

    # Compute indicators
    vwap = compute_vwap(bars)
    if vwap <= 0:
        return None

    last = bars[-1]
    prev = bars[-2] if len(bars) > 1 else last
    
    # Validate bar integrity
    if not hasattr(last, 'close') or not hasattr(last, 'volume'):
        return None
    
    # Core metrics
    dist_pct = (last.close - vwap) / vwap * 100
    mom = last.close - bars[-1 - cfg.momentum_lookback].close
    
    # Volume confirmation
    baseline = avg_volume(bars[:-1], 20)
    vol_spike = baseline > 0 and last.volume >= baseline * cfg.volume_spike_mult
    
    if not vol_spike:
        return None
    
    # RSI confirmation (avoid entering at extremes)
    closes = [b.close for b in bars]
    rsi = compute_rsi(closes, period=14)
    
    # Price action confirmation
    crossed_up = prev.close <= vwap and last.close > vwap
    crossed_down = prev.close >= vwap and last.close < vwap
    
    # LONG signal: cross above VWAP + volume + positive momentum + RSI not overbought
    if (
        (crossed_up or dist_pct > cfg.vwap_min_distance_pct)
        and mom > 0
        and last.close > vwap
        and (rsi is None or rsi < cfg.rsi_overbought)
    ):
        vol_ratio = last.volume / baseline if baseline > 0 else 0
        # Confidence: higher volume ratio, closer to VWAP, healthier RSI
        confidence = min(
            1.0,
            0.5  # base
            + 0.2 * min(vol_ratio / 3, 1.0)  # volume contribution
            + 0.2 * (1 - abs(dist_pct) / 0.2)  # distance from VWAP
            + 0.1 * (1 - (rsi or 50) / cfg.rsi_overbought)  # RSI headroom
        )
        return Signal(
            Side.LONG,
            f"VWAP+ vol x{vol_ratio:.1f} rsi {rsi:.0f}" if rsi else f"VWAP+ vol x{vol_ratio:.1f}",
            confidence
        )
    
    # SHORT signal: cross below VWAP + volume + negative momentum + RSI not oversold
    if (
        (crossed_down or dist_pct < -cfg.vwap_min_distance_pct)
        and mom < 0
        and last.close < vwap
        and (rsi is None or rsi > cfg.rsi_oversold)
    ):
        vol_ratio = last.volume / baseline if baseline > 0 else 0
        confidence = min(
            1.0,
            0.5  # base
            + 0.2 * min(vol_ratio / 3, 1.0)  # volume contribution
            + 0.2 * (1 - abs(dist_pct) / 0.2)  # distance from VWAP
            + 0.1 * ((rsi or 50) / cfg.rsi_oversold)  # RSI headroom
        )
        return Signal(
            Side.SHORT,
            f"VWAP- vol x{vol_ratio:.1f} rsi {rsi:.0f}" if rsi else f"VWAP- vol x{vol_ratio:.1f}",
            confidence
        )
    
    return None

