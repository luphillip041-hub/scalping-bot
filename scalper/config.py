"""Configuration loaded from environment variables. See .env.example."""
import os
from dataclasses import dataclass, field

from dotenv import load_dotenv

load_dotenv()


@dataclass
class Config:
    # Alpaca
    api_key: str = os.getenv("ALPACA_API_KEY", "")
    api_secret: str = os.getenv("ALPACA_API_SECRET", "")
    paper: bool = os.getenv("ALPACA_PAPER", "true").lower() == "true"

    # Universe: liquid, tight-spread names work best for scalping
    # Default: 20 mega-cap tech + growth + finance stocks (all >$1T / >$100B market cap)
    # Expanded from 3 → 20 tickers for better diversification and opportunity surface
    symbols: list = field(
        default_factory=lambda: os.getenv(
            "SYMBOLS",
            # Tech & Cloud: highest volume, tightest spreads
            "AAPL,MSFT,NVDA,TSLA,META,"
            # Growth & Payment: consistent volume
            "AMZN,GOOGL,NFLX,GOOG,"
            # Financial Services: diverse market conditions
            "JPM,BAC,GS,BLK,V,MA,"
            # Semiconductors: volatile, good scalping moves
            "AMD,QCOM,MU,AVGO,"
            # Industrials/Discretionary: broader market correlation
            "BA,F"
        ).split(",")
    )

    # Strategy (defaults tuned via 40-day backtest: PF 1.18, +$403)
    bar_timeframe: str = os.getenv("BAR_TIMEFRAME", "1Min")
    vwap_min_distance_pct: float = float(os.getenv("VWAP_MIN_DISTANCE_PCT", "0.05"))
    volume_spike_mult: float = float(os.getenv("VOLUME_SPIKE_MULT", "2.0"))
    momentum_lookback: int = int(os.getenv("MOMENTUM_LOOKBACK", "5"))
    take_profit_pct: float = float(os.getenv("TAKE_PROFIT_PCT", "0.5"))
    stop_loss_pct: float = float(os.getenv("STOP_LOSS_PCT", "0.3"))
    max_hold_minutes: int = int(os.getenv("MAX_HOLD_MINUTES", "15"))

    # Risk
    position_size_usd: float = float(os.getenv("POSITION_SIZE_USD", "2000"))
    max_positions: int = int(os.getenv("MAX_POSITIONS", "5"))  # expanded from 3
    max_daily_loss_usd: float = float(os.getenv("MAX_DAILY_LOSS_USD", "300"))  # expanded from 150
    max_trades_per_day: int = int(os.getenv("MAX_TRADES_PER_DAY", "50"))  # expanded from 25

    # Session (ET): avoid first/last 5 min of the open/close
    trade_start: str = os.getenv("TRADE_START", "09:35")
    trade_end: str = os.getenv("TRADE_END", "15:50")

    poll_seconds: int = int(os.getenv("POLL_SECONDS", "20"))

    def validate(self):
        if not self.api_key or not self.api_secret:
            raise ValueError("Set ALPACA_API_KEY and ALPACA_API_SECRET in .env")

