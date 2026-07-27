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

    # Universe: expanded to 35 liquid mega-cap names across sectors
    # Criteria: >$100B market cap, avg volume >10M shares/day, tight spreads
    symbols: list = field(
        default_factory=lambda: os.getenv(
            "SYMBOLS",
            # Tech & Cloud (highest volume, tightest spreads, consistent momentum)
            "AAPL,MSFT,NVDA,TSLA,META,AMZN,GOOGL,GOOG,NFLX,"
            # Semiconductors (volatile, good scalping moves)
            "AMD,QCOM,AVGO,MU,BROADCOM,"
            # Financial Services (diverse market conditions, liquid)
            "JPM,BAC,GS,BLK,V,MA,PYPL,"
            # Industrials & Discretionary (broader market exposure)
            "BA,CAT,GE,F,GM,"
            # Consumer & Retail (cyclical, good for mean reversion)
            "COST,HD,WMT,TJX,LOW,"
            # Healthcare (steady flows, lower volatility for risk mgmt)
            "UNH,JNJ,PFE,ABBV,"
            # Utilities & Energy (hedge against tech drawdowns)
            "XOM,CVX"
        ).split(",")
    )

    # Strategy parameters (tuned via backtesting for 35-ticker pool)
    bar_timeframe: str = os.getenv("BAR_TIMEFRAME", "1Min")
    
    # Entry filters: multi-indicator confirmation
    vwap_min_distance_pct: float = float(os.getenv("VWAP_MIN_DISTANCE_PCT", "0.08"))
    volume_spike_mult: float = float(os.getenv("VOLUME_SPIKE_MULT", "2.5"))
    momentum_lookback: int = int(os.getenv("MOMENTUM_LOOKBACK", "5"))
    rsi_overbought: float = float(os.getenv("RSI_OVERBOUGHT", "70"))
    rsi_oversold: float = float(os.getenv("RSI_OVERSOLD", "30"))
    min_bars_for_entry: int = int(os.getenv("MIN_BARS_FOR_ENTRY", "20"))
    
    # Exit targets: take profit / stop loss
    take_profit_pct: float = float(os.getenv("TAKE_PROFIT_PCT", "0.6"))
    stop_loss_pct: float = float(os.getenv("STOP_LOSS_PCT", "0.4"))
    max_hold_minutes: int = int(os.getenv("MAX_HOLD_MINUTES", "10"))
    
    # Scaling: position size adapts to volatility
    base_position_size_usd: float = float(os.getenv("BASE_POSITION_SIZE_USD", "2000"))
    position_size_usd: float = base_position_size_usd  # keep for backward compat
    position_size_scaling_enabled: bool = os.getenv("POSITION_SIZE_SCALING", "true").lower() == "true"

    # Risk management
    max_positions: int = int(os.getenv("MAX_POSITIONS", "8"))
    max_daily_loss_usd: float = float(os.getenv("MAX_DAILY_LOSS_USD", "500"))
    max_trades_per_day: int = int(os.getenv("MAX_TRADES_PER_DAY", "100"))
    max_daily_wins_pct: float = float(os.getenv("MAX_DAILY_WINS_PCT", "20.0"))

    # Session (ET): avoid first/last 5 min of the open/close
    trade_start: str = os.getenv("TRADE_START", "09:35")
    trade_end: str = os.getenv("TRADE_END", "15:50")

    # Polling and data fetch
    poll_seconds: int = int(os.getenv("POLL_SECONDS", "15"))
    bars_cache_ttl_seconds: int = int(os.getenv("BARS_CACHE_TTL", "5"))

    # Metrics & analytics
    enable_metrics: bool = os.getenv("ENABLE_METRICS", "true").lower() == "true"
    metrics_log_file: str = os.getenv("METRICS_LOG_FILE", "/tmp/scalper_metrics.jsonl")

    def validate(self):
        if not self.api_key or not self.api_secret:
            raise ValueError("Set ALPACA_API_KEY and ALPACA_API_SECRET in .env")
        if self.max_positions < 1:
            raise ValueError("MAX_POSITIONS must be >= 1")
        if self.max_daily_loss_usd <= 0:
            raise ValueError("MAX_DAILY_LOSS_USD must be > 0")

