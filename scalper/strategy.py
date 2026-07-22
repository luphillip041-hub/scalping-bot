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
    """Session VWAP from a list of bars (each needs .high .low .close .volume)."""
    pv = sum(((b.high + b.low + b.close) / 3) * b.volume for b in bars)
    vol = sum(b.volume for b in bars)
    return pv / vol if vol else 0.0


def avg_volume(bars, n: int) -> float:
    tail = bars[-n:] if len(bars) >= n else bars
    vols = [b.volume for b in tail]
    return sum(vols) / len(vols) if vols else 0.0


def generate_signal(bars, cfg) -> Signal | None:
    """Return a Signal or None. `bars` = today's 1-min bars, oldest first."""
    if len(bars) < cfg.momentum_lookback + 5:
        return None

    vwap = compute_vwap(bars)
    if vwap == 0:
        return None

    last = bars[-1]
    prev = bars[-2]
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
        return Signal(Side.LONG, f"VWAP cross up + vol x{last.volume / baseline:.1f}")
    if (
        (crossed_down or dist_pct < -cfg.vwap_min_distance_pct)
        and mom < 0
        and last.close < vwap
    ):
        return Signal(Side.SHORT, f"VWAP cross down + vol x{last.volume / baseline:.1f}")
    return None
