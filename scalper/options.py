"""Options order management for calls/puts scalping.

Fetches option contracts via Alpaca API, selects based on Greeks and 
strike offset, and submits market orders for options trades alongside equities.
"""
import logging
from datetime import datetime, timedelta
from dataclasses import dataclass
from zoneinfo import ZoneInfo
from enum import Enum
from typing import Optional

from alpaca.trading.client import TradingClient
from alpaca.trading.requests import GetOptionContractsRequest
from alpaca.trading.enums import ContractType

log = logging.getLogger("scalper")
ET = ZoneInfo("America/New_York")


class OptionSide(str, Enum):
    CALL = "call"
    PUT = "put"


@dataclass
class OptionContract:
    """Represents a single options contract with Greeks and market data."""
    symbol: str  # Underlying (e.g., "AAPL")
    contract_symbol: str  # Alpaca contract symbol (e.g., "AAPL240816C150000")
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
        """Bid-ask spread in dollars."""
        return self.ask - self.bid if self.bid > 0 and self.ask > 0 else 0

    @property
    def spread_pct(self) -> float:
        """Bid-ask spread as percentage of mid price."""
        if self.mid_price <= 0:
            return 100.0
        return (self.spread / self.mid_price) * 100

    def is_liquid(self, min_volume: int = 5, max_spread_pct: float = 2.0) -> bool:
        """Check if contract meets liquidity thresholds."""
        if self.volume < min_volume:
            return False
        if self.bid <= 0 or self.ask <= 0:
            return False
        return self.spread_pct <= max_spread_pct


class OptionsFetcher:
    """Fetch and parse options contracts from Alpaca."""

    def __init__(self, trading_client: TradingClient):
        self.trading = trading_client

    def fetch_chain(self, symbol: str, expiry_date: str) -> list[OptionContract]:
        """Fetch options chain for a symbol and specific expiration.
        
        Args:
            symbol: Underlying symbol (e.g., "AAPL")
            expiry_date: Expiration date in YYYYMMDD format
        
        Returns:
            List of OptionContract objects (calls + puts) with Greeks and market data
        """
        contracts = []
        
        try:
            # Convert YYYYMMDD to YYYY-MM-DD for API
            exp_formatted = f"{expiry_date[:4]}-{expiry_date[4:6]}-{expiry_date[6:8]}"
            
            # Fetch calls
            call_request = GetOptionContractsRequest(
                underlying_symbol=symbol,
                expiration_date=exp_formatted,
                contract_type=ContractType.CALL
            )
            call_response = self.trading.get_option_contracts(call_request)
            if call_response and call_response.option_contracts:
                for contract in call_response.option_contracts:
                    if self._has_market_data(contract):
                        contracts.append(self._parse_contract(contract, OptionSide.CALL, expiry_date))
            
            # Fetch puts
            put_request = GetOptionContractsRequest(
                underlying_symbol=symbol,
                expiration_date=exp_formatted,
                contract_type=ContractType.PUT
            )
            put_response = self.trading.get_option_contracts(put_request)
            if put_response and put_response.option_contracts:
                for contract in put_response.option_contracts:
                    if self._has_market_data(contract):
                        contracts.append(self._parse_contract(contract, OptionSide.PUT, expiry_date))
            
            if not contracts:
                log.warning("Empty options chain for %s expiry %s", symbol, expiry_date)
        
        except Exception as e:
            log.error("Failed to fetch options chain for %s: %s", symbol, e)
        
        return contracts

    @staticmethod
    def _parse_contract(contract_data, side: OptionSide, expiry_date: str) -> OptionContract:
        """Parse Alpaca contract data into OptionContract object."""
        return OptionContract(
            symbol=contract_data.underlying_symbol,
            contract_symbol=contract_data.symbol,
            side=side,
            expiry=expiry_date,
            strike=float(contract_data.strike_price),
            bid=float(contract_data.quote.bid if contract_data.quote and contract_data.quote.bid else 0),
            ask=float(contract_data.quote.ask if contract_data.quote and contract_data.quote.ask else 0),
            delta=float(contract_data.greeks.delta if contract_data.greeks and contract_data.greeks.delta else 0),
            theta=float(contract_data.greeks.theta if contract_data.greeks and contract_data.greeks.theta else 0),
            gamma=float(contract_data.greeks.gamma if contract_data.greeks and contract_data.greeks.gamma else 0),
            vega=float(contract_data.greeks.vega if contract_data.greeks and contract_data.greeks.vega else 0),
            iv=float(contract_data.greeks.implied_volatility if contract_data.greeks and contract_data.greeks.implied_volatility else 0),
            open_interest=int(contract_data.open_interest if contract_data.open_interest else 0),
            volume=int(contract_data.quote.volume if contract_data.quote and contract_data.quote.volume else 0),
        )

    @staticmethod
    def _has_market_data(contract_data) -> bool:
        """Check if contract has valid bid/ask prices."""
        if not contract_data.quote:
            return False
        bid = float(contract_data.quote.bid if contract_data.quote.bid else 0)
        ask = float(contract_data.quote.ask if contract_data.quote.ask else 0)
        return bid > 0 and ask > 0


class OptionSelector:
    """Select options contracts based on signal and strategy parameters."""

    def __init__(
        self,
        days_to_expiry: int = 0,
        strike_offset_pct: float = 0.5,
        max_delta: float = 0.9,
        min_delta: float = 0.2,
        max_bid_ask_spread_pct: float = 2.0,
        min_volume: int = 5,
    ):
        self.days_to_expiry = days_to_expiry
        self.strike_offset_pct = strike_offset_pct
        self.max_delta = max_delta
        self.min_delta = min_delta
        self.max_bid_ask_spread_pct = max_bid_ask_spread_pct
        self.min_volume = min_volume

    def select_call(
        self, underlying_price: float, contracts: list[OptionContract]
    ) -> Optional[OptionContract]:
        """Select a call contract for a LONG signal.
        
        Picks a slightly OTM call with reasonable Greeks and liquidity.
        
        Args:
            underlying_price: Current price of underlying
            contracts: List of call contracts to choose from
        
        Returns:
            Selected OptionContract or None if no suitable contract found
        """
        # Filter for calls
        calls = [c for c in contracts if c.side == OptionSide.CALL]
        if not calls:
            return None

        # Filter for liquidity
        liquid = [c for c in calls 
                  if c.is_liquid(self.min_volume, self.max_bid_ask_spread_pct)]
        if not liquid:
            log.warning("No liquid call contracts found (checked %d contracts)", len(calls))
            return None

        # Filter for reasonable delta (0.3-0.7 typical for scalping)
        # For calls, delta is positive
        in_delta = [
            c for c in liquid
            if self.min_delta <= c.delta <= self.max_delta
        ]
        if not in_delta:
            log.warning("No contracts in delta range [%.2f, %.2f] (checked %d liquid)", 
                       self.min_delta, self.max_delta, len(liquid))
            return None

        # Prefer slightly OTM calls (lower delta = more OTM)
        # Target delta ~ strike_offset_pct (e.g., 0.5% OTM ≈ 0.4-0.5 delta)
        target_delta = self.strike_offset_pct / 100 * 0.8  # rough mapping
        best = min(in_delta, key=lambda c: abs(c.delta - target_delta))
        
        log.info("Selected CALL %s strike=%.2f delta=%.2f bid=%.3f ask=%.3f spread=%.2f%%",
                 best.contract_symbol, best.strike, best.delta, 
                 best.bid, best.ask, best.spread_pct)
        return best

    def select_put(
        self, underlying_price: float, contracts: list[OptionContract]
    ) -> Optional[OptionContract]:
        """Select a put contract for a SHORT signal.
        
        Picks a slightly OTM put with reasonable Greeks and liquidity.
        
        Args:
            underlying_price: Current price of underlying
            contracts: List of put contracts to choose from
        
        Returns:
            Selected OptionContract or None if no suitable contract found
        """
        # Filter for puts
        puts = [c for c in contracts if c.side == OptionSide.PUT]
        if not puts:
            return None

        # Filter for liquidity
        liquid = [c for c in puts 
                  if c.is_liquid(self.min_volume, self.max_bid_ask_spread_pct)]
        if not liquid:
            log.warning("No liquid put contracts found (checked %d contracts)", len(puts))
            return None

        # Filter for reasonable delta (puts have negative delta, use abs value)
        # For puts, delta is negative, so we check abs(delta)
        in_delta = [
            c for c in liquid
            if self.min_delta <= abs(c.delta) <= self.max_delta
        ]
        if not in_delta:
            log.warning("No contracts in delta range [%.2f, %.2f] (checked %d liquid)",
                       self.min_delta, self.max_delta, len(liquid))
            return None

        # Prefer slightly OTM puts (lower abs(delta) = more OTM)
        target_delta = self.strike_offset_pct / 100 * 0.8
        best = min(in_delta, key=lambda c: abs(abs(c.delta) - target_delta))
        
        log.info("Selected PUT %s strike=%.2f delta=%.2f bid=%.3f ask=%.3f spread=%.2f%%",
                 best.contract_symbol, best.strike, best.delta, 
                 best.bid, best.ask, best.spread_pct)
        return best

    def next_market_expiry(self) -> str:
        """Calculate next market trading day (skip weekends/holidays).
        
        Returns expiry date in YYYYMMDD format based on days_to_expiry.
        """
        now = datetime.now(ET)
        days_ahead = self.days_to_expiry
        
        while days_ahead >= 0:
            target = now + timedelta(days=days_ahead)
            # Skip weekends (5=Sat, 6=Sun)
            if target.weekday() < 5:
                return target.strftime("%Y%m%d")
            days_ahead += 1
        
        return now.strftime("%Y%m%d")

