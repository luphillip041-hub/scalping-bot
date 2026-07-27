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
from alpaca.data.historical.option import OptionHistoricalDataClient
from alpaca.data.requests import OptionLatestQuoteRequest

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
    """Fetch and parse options contracts from Alpaca with quotes and Greeks."""

    def __init__(self, trading_client: TradingClient, data_client: OptionHistoricalDataClient):
        self.trading = trading_client
        self.data = data_client

    def fetch_chain(self, symbol: str, expiry_date: str) -> list[OptionContract]:
        """Fetch options chain for a symbol and specific expiration.
        
        Args:
            symbol: Underlying symbol (e.g., "AAPL")
            expiry_date: Expiration date in YYYYMMDD format
        
        Returns:
            List of OptionContract objects (calls + puts) with Greeks and market data
        """
        contracts = []
        calls_parsed = 0
        puts_parsed = 0
        
        try:
            # Convert YYYYMMDD to YYYY-MM-DD for API
            exp_formatted = f"{expiry_date[:4]}-{expiry_date[4:6]}-{expiry_date[6:8]}"
            log.info("Fetching options chain for %s expiry %s (%s)", symbol, expiry_date, exp_formatted)
            
            # Fetch call contracts
            call_request = GetOptionContractsRequest(
                underlying_symbol=symbol,
                expiration_date=exp_formatted,
                contract_type=ContractType.CALL
            )
            call_response = self.trading.get_option_contracts(call_request)
            
            if call_response and call_response.option_contracts:
                log.info("Got %d CALL contracts for %s %s", len(call_response.option_contracts), symbol, expiry_date)
                for i, contract_meta in enumerate(call_response.option_contracts):
                    contract = self._fetch_contract_with_quote(contract_meta, OptionSide.CALL, expiry_date, i)
                    if contract:
                        contracts.append(contract)
                        calls_parsed += 1
            else:
                log.warning("No CALL contracts returned for %s %s", symbol, exp_formatted)
            
            # Fetch put contracts
            put_request = GetOptionContractsRequest(
                underlying_symbol=symbol,
                expiration_date=exp_formatted,
                contract_type=ContractType.PUT
            )
            put_response = self.trading.get_option_contracts(put_request)
            
            if put_response and put_response.option_contracts:
                log.info("Got %d PUT contracts for %s %s", len(put_response.option_contracts), symbol, expiry_date)
                for i, contract_meta in enumerate(put_response.option_contracts):
                    contract = self._fetch_contract_with_quote(contract_meta, OptionSide.PUT, expiry_date, i)
                    if contract:
                        contracts.append(contract)
                        puts_parsed += 1
            else:
                log.warning("No PUT contracts returned for %s %s", symbol, exp_formatted)
            
            log.info("Total options contracts fetched for %s: %d (calls: %d, puts: %d)", 
                     symbol, len(contracts), calls_parsed, puts_parsed)
            if not contracts:
                log.warning("Empty options chain for %s expiry %s", symbol, expiry_date)
        
        except Exception as e:
            log.error("Failed to fetch options chain for %s: %s", symbol, e, exc_info=True)
        
        return contracts

    def _fetch_contract_with_quote(
        self, contract_meta, side: OptionSide, expiry_date: str, index: int = 0
    ) -> Optional[OptionContract]:
        """Fetch quote and Greeks for a single contract.
        
        Args:
            contract_meta: OptionContract metadata from get_option_contracts()
            side: CALL or PUT
            expiry_date: Expiration in YYYYMMDD format
            index: Index in list (for logging)
        
        Returns:
            OptionContract with quote data, or None if fetch fails
        """
        try:
            # Get latest quote for this contract symbol
            quote_request = OptionLatestQuoteRequest(symbol_or_symbols=contract_meta.symbol)
            quote_response = self.data.get_option_latest_quote(quote_request)
            
            if not quote_response:
                log.warning("Quote response EMPTY for %s[%d]", contract_meta.symbol, index)
                return None
            
            if contract_meta.symbol not in quote_response:
                log.warning("Contract %s[%d] NOT IN quote response. Keys: %s", 
                           contract_meta.symbol, index, 
                           list(quote_response.keys())[:5] if quote_response else "[]")
                return None
            
            quote = quote_response[contract_meta.symbol]
            
            # Extract bid/ask
            bid = float(quote.bid_price) if quote.bid_price else 0
            ask = float(quote.ask_price) if quote.ask_price else 0
            
            if bid <= 0 or ask <= 0:
                log.warning("Invalid bid/ask for %s[%d]: bid=%.3f ask=%.3f", 
                           contract_meta.symbol, index, bid, ask)
                return None
            
            # Extract Greeks (may be None)
            delta = float(quote.delta) if quote.delta else 0.0
            theta = float(quote.theta) if quote.theta else 0.0
            gamma = float(quote.gamma) if quote.gamma else 0.0
            vega = float(quote.vega) if quote.vega else 0.0
            iv = float(quote.implied_volatility) if quote.implied_volatility else 0.0
            volume = int(quote.last_quote_volume) if quote.last_quote_volume else 0
            
            log.info("Parsed %s[%d] %s: %.2f / %.2f delta=%.2f", 
                    contract_meta.symbol, index, side.value, bid, ask, delta)
            
            return OptionContract(
                symbol=contract_meta.underlying_symbol,
                contract_symbol=contract_meta.symbol,
                side=side,
                expiry=expiry_date,
                strike=float(contract_meta.strike_price),
                bid=bid,
                ask=ask,
                delta=delta,
                theta=theta,
                gamma=gamma,
                vega=vega,
                iv=iv,
                volume=volume,
            )
        
        except Exception as e:
            log.warning("Exception for %s[%d]: %s", contract_meta.symbol, index, e)
            return None


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
        """Select a call contract for a LONG signal."""
        calls = [c for c in contracts if c.side == OptionSide.CALL]
        if not calls:
            return None

        liquid = [c for c in calls 
                  if c.is_liquid(self.min_volume, self.max_bid_ask_spread_pct)]
        if not liquid:
            log.warning("No liquid call contracts found (checked %d contracts)", len(calls))
            return None

        in_delta = [
            c for c in liquid
            if self.min_delta <= c.delta <= self.max_delta
        ]
        if not in_delta:
            log.warning("No contracts in delta range [%.2f, %.2f] (checked %d liquid)", 
                       self.min_delta, self.max_delta, len(liquid))
            return None

        target_delta = self.strike_offset_pct / 100 * 0.8
        best = min(in_delta, key=lambda c: abs(c.delta - target_delta))
        
        log.info("Selected CALL %s strike=%.2f delta=%.2f bid=%.3f ask=%.3f spread=%.2f%%",
                 best.contract_symbol, best.strike, best.delta, 
                 best.bid, best.ask, best.spread_pct)
        return best

    def select_put(
        self, underlying_price: float, contracts: list[OptionContract]
    ) -> Optional[OptionContract]:
        """Select a put contract for a SHORT signal."""
        puts = [c for c in contracts if c.side == OptionSide.PUT]
        if not puts:
            return None

        liquid = [c for c in puts 
                  if c.is_liquid(self.min_volume, self.max_bid_ask_spread_pct)]
        if not liquid:
            log.warning("No liquid put contracts found (checked %d contracts)", len(puts))
            return None

        in_delta = [
            c for c in liquid
            if self.min_delta <= abs(c.delta) <= self.max_delta
        ]
        if not in_delta:
            log.warning("No contracts in delta range [%.2f, %.2f] (checked %d liquid)",
                       self.min_delta, self.max_delta, len(liquid))
            return None

        target_delta = self.strike_offset_pct / 100 * 0.8
        best = min(in_delta, key=lambda c: abs(abs(c.delta) - target_delta))
        
        log.info("Selected PUT %s strike=%.2f delta=%.2f bid=%.3f ask=%.3f spread=%.2f%%",
                 best.contract_symbol, best.strike, best.delta, 
                 best.bid, best.ask, best.spread_pct)
        return best

    def next_market_expiry(self) -> str:
        """Calculate next market trading day (skip weekends/holidays)."""
        now = datetime.now(ET)
        target = now + timedelta(days=self.days_to_expiry)
        
        while target.weekday() >= 5:
            target += timedelta(days=1)
        
        return target.strftime("%Y%m%d")

