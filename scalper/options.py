"""Options order management for calls/puts scalping.

Fetches option chains, selects contracts based on Greeks and strike offset,
and submits bracket orders for options trades alongside equities.
"""
import logging
from datetime import datetime, timedelta
from dataclasses import dataclass
from zoneinfo import ZoneInfo
from enum import Enum

log = logging.getLogger("scalper")
ET = ZoneInfo("America/New_York")


class OptionSide(str, Enum):
    CALL = "call"
    PUT = "put"


@dataclass
class OptionContract:
    symbol: str  # Underlying (e.g., "AAPL")
    contract_id: str  # Alpaca contract symbol (e.g., "AAPL240816C150000")
    side: OptionSide
    expiry: str  # "YYYYMMDD"
    strike: float
    bid: float
    ask: float
    delta: float = 0.0
    theta: float = 0.0
    gamma: float = 0.0
    vega: float = 0.0
    iv: float = 0.0  # implied volatility
    open_interest: int = 0
    volume: int = 0

    @property
    def mid_price(self) -> float:
        """Mid price between bid and ask."""
        if self.bid > 0 and self.ask > 0:
            return (self.bid + self.ask) / 2
        return self.ask if self.ask > 0 else self.bid

    @property
    def spread(self) -> float:
        """Bid-ask spread."""
        return self.ask - self.bid if self.bid > 0 and self.ask > 0 else 0

    def is_liquid(self, min_volume: int = 5, max_spread_pct: float = 2.0) -> bool:
        """Check if contract meets liquidity thresholds."""
        if self.volume < min_volume:
            return False
        if self.bid <= 0 or self.ask <= 0:
            return False
        spread_pct = (self.spread / self.mid_price * 100) if self.mid_price > 0 else 100
        return spread_pct <= max_spread_pct


class OptionSelector:
    """Select options contracts based on signal and strategy parameters."""

    def __init__(
        self,
        days_to_expiry: int = 0,  # 0 = same day, 1+ = future expiries
        strike_offset_pct: float = 0.5,  # e.g., 0.5% OTM for calls
        max_delta: float = 0.9,
        min_delta: float = 0.2,
        min_theta: float = -0.5,  # want theta decay to work for us
        max_bid_ask_spread_pct: float = 2.0,
        min_volume: int = 5,
    ):
        self.days_to_expiry = days_to_expiry
        self.strike_offset_pct = strike_offset_pct
        self.max_delta = max_delta
        self.min_delta = min_delta
        self.min_theta = min_theta
        self.max_bid_ask_spread_pct = max_bid_ask_spread_pct
        self.min_volume = min_volume

    def select_call(
        self, underlying_price: float, contracts: list[OptionContract]
    ) -> OptionContract | None:
        """Select a call contract for a LONG signal.
        
        Picks a slightly OTM call with reasonable Greeks and liquidity.
        """
        # Filter for calls
        calls = [c for c in contracts if c.side == OptionSide.CALL]
        if not calls:
            return None

        # Filter for liquidity
        liquid = [c for c in calls if c.is_liquid(self.min_volume, self.max_bid_ask_spread_pct)]
        if not liquid:
            return None

        # Filter for reasonable Greeks (call delta typically 0.3–0.7 for scalping)
        in_delta = [
            c for c in liquid
            if self.min_delta <= abs(c.delta) <= self.max_delta
        ]
        if not in_delta:
            return None

        # Prefer slightly OTM strikes (lower delta = more OTM)
        # Sort by delta ascending, take the one closest to strike_offset_pct
        target_delta = abs(self.strike_offset_pct / 100)  # rough approximation
        best = min(in_delta, key=lambda c: abs(c.delta - target_delta))
        
        log.debug(
            "Selected CALL %s strike=%.2f delta=%.2f bid=%.2f ask=%.2f",
            best.contract_id,
            best.strike,
            best.delta,
            best.bid,
            best.ask,
        )
        return best

    def select_put(
        self, underlying_price: float, contracts: list[OptionContract]
    ) -> OptionContract | None:
        """Select a put contract for a SHORT signal.
        
        Picks a slightly OTM put with reasonable Greeks and liquidity.
        """
        # Filter for puts
        puts = [c for c in contracts if c.side == OptionSide.PUT]
        if not puts:
            return None

        # Filter for liquidity
        liquid = [c for c in puts if c.is_liquid(self.min_volume, self.max_bid_ask_spread_pct)]
        if not liquid:
            return None

        # Filter for reasonable Greeks
        in_delta = [
            c for c in liquid
            if self.min_delta <= abs(c.delta) <= self.max_delta
        ]
        if not in_delta:
            return None

        # Prefer slightly OTM strikes
        target_delta = abs(self.strike_offset_pct / 100)
        best = min(in_delta, key=lambda c: abs(abs(c.delta) - target_delta))
        
        log.debug(
            "Selected PUT %s strike=%.2f delta=%.2f bid=%.2f ask=%.2f",
            best.contract_id,
            best.strike,
            best.delta,
            best.bid,
            best.ask,
        )
        return best

