"""VWAP + momentum scalping strategy.

Entry (long):  price crosses above VWAP with a volume spike and positive
               short-term momentum -> ride the burst.
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


def compute_vwap(bars) -> float:
    """Session VWAP from a list of bars (each needs .high .low .close .volume).
    
    Returns 0 if no valid bars or zero volume.
    """
    if not bars:
        return 0.0
    
    pv = sum(((b.high + b.low + b.close) / 3) * b.volume for b in bars)
    vol = sum(b.volume for b in bars)
    return pv / vol if vol > 0 else 0.0


def avg_volume(bars, n: int) -> float:
    """Average volume of the last n bars (or fewer if fewer bars exist)."""
    if not bars:
        return 0.0
    tail = bars[-n:] if len(bars) >= n else bars
    vols = [b.volume for b in tail]
    return sum(vols) / len(vols) if vols else 0.0


def generate_signal(bars, cfg) -> Signal | None:
    """Return a Signal or None. `bars` = today's 1-min bars, oldest first.
    
    Validates sufficient bar count before computing indicators.
    """
    # Require minimum bars for momentum lookback + buffer
    min_bars = cfg.momentum_lookback + 5
    if len(bars) < min_bars:
        return None

    vwap = compute_vwap(bars)
    if vwap <= 0:
        return None

    last = bars[-1]
    prev = bars[-2]
    
    # Validate bar integrity
    if not hasattr(last, 'close') or not hasattr(last, 'volume'):
        return None
    
    dist_pct = (last.close - vwap) / vwap * 100

    # momentum: close higher/lower than N bars ago
    mom = last.close - bars[-1 - cfg.momentum_lookback].close

    # volume spike vs recent average (exclude current bar from baseline)
    baseline = avg_volume(bars[:-1], 20)
    vol_spike = baseline > 0 and last.volume >= baseline * cfg.volume_spike_mult

    if not vol_spike:
        return None

    crossed_up = prev.close <= vwap and last.close > vwap
    crossed_down = prev.close >= vwap and last.close < vwap

    if (
        (crossed_up or dist_pct > cfg.vwap_min_distance_pct)
        and mom > 0
        and last.close > vwap
    ):
        vol_ratio = last.volume / baseline if baseline > 0 else 0
        return Signal(Side.LONG, f"VWAP cross up + vol x{vol_ratio:.1f}")
    
    if (
        (crossed_down or dist_pct < -cfg.vwap_min_distance_pct)
        and mom < 0
        and last.close < vwap
    ):
        vol_ratio = last.volume / baseline if baseline > 0 else 0
        return Signal(Side.SHORT, f"VWAP cross down + vol x{vol_ratio:.1f}")
    
    return None

