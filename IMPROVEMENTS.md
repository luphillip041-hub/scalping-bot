# Scalping Bot Improvements

This update introduces several enhancements to improve performance, reduce false signals, and provide better trade tracking.

## Key Improvements

### 1. **Trade-by-Trade PnL Tracking**
- `TradeRecord` class tracks every trade from entry to exit with exact PnL
- Improved from approximations to precise realized P&L calculations
- Each trade stores: entry/exit price, quantity, time held, close reason

### 2. **Volatility-Adjusted Position Sizing**
- Position size now scales based on current intraday volatility
- Higher volatility → smaller positions (risk reduction)
- Estimated using returns variance over the last 20 bars
- Prevents over-sizing in choppy markets

### 3. **Enhanced Signal Generation**
- Added **RSI filter** (70 overbought / 30 oversold) to avoid exhaustion entries
- Confidence scoring (0.0–1.0) on each signal
- Only trade signals with >50% confidence
- Improved momentum calculation with percentage-based scoring
- Better volume spike detection

### 4. **Consecutive Loss Circuit Breaker**
- Halts trading after N consecutive losing trades
- Prevents cascading losses during drawdowns
- Resets next session
- Configurable via `max_consecutive_losses`

### 5. **Signal Rate Limiting**
- 60-second cooldown per symbol after order failure or false signal
- Prevents hammer-clicking on failing symbols
- Reduces API errors from rapid re-entries

### 6. **Performance Metrics Tracking**
- Real-time win rate, profit factor, max drawdown
- Session summary: trades, wins, losses, hold times
- Daily recap with comprehensive stats
- Per-trade analysis: PnL %, hold time, close reason

### 7. **Trade Journal (New Module)**
- Persistent JSONL trade log (`trades.jsonl`)
- `TradeLog` class for querying historical trades
- Monthly performance summaries
- Best-performing symbols analysis

### 8. **Better Risk Metrics**
- Profit Factor calculation
- Win Rate percentage
- Peak-to-drawdown tracking
- Session-by-session metrics

## Configuration Changes

New optional environment variables:

```bash
# Signal filtering
MOMENTUM_LOOKBACK=5              # bars back for momentum calc
VWAP_MIN_DISTANCE_PCT=0.05       # min % distance from VWAP
VOLUME_SPIKE_MULT=2.0            # volume threshold multiplier

# Position management
POSITION_SIZE_USD=2000           # base position size (adjusted for volatility)
MAX_POSITIONS=3                  # concurrent positions
MAX_HOLD_MINUTES=15              # max time to hold a trade

# Risk control
MAX_DAILY_LOSS_USD=150           # daily loss limit
MAX_TRADES_PER_DAY=25            # daily trade cap
STOP_LOSS_PCT=0.3                # SL below entry
TAKE_PROFIT_PCT=0.5              # TP above entry

# Execution
POLL_SECONDS=20                  # loop interval
TRADE_START="09:35"              # session start (ET)
TRADE_END="15:50"                # session end (ET)
```

## Code Structure

```
scalper/
├── bot.py           # Main loop + TradeRecord class (UPDATED)
├── strategy.py      # Signal generation w/ RSI + confidence (UPDATED)
├── risk.py          # Risk mgmt + performance tracking (UPDATED)
├── analytics.py     # Trade journal + stats (NEW)
├── notify.py        # Discord notifications (unchanged)
└── config.py        # Config loading (unchanged)
```

## Usage

### Live Trading
```bash
python main.py
```

### Backtesting (40 days, custom symbols)
```bash
DAYS=40 SYMBOLS=AAPL,TSLA,META python backtest.py
```

### Analyzing Trade History
```python
from scalper.analytics import PerformanceTracker

pt = PerformanceTracker("trades.jsonl")
print(pt.monthly_summary())
print(pt.best_symbols(limit=5))
```

## Monitoring Discord Notifications

The bot now sends richer notifications:

**Entry signals:**
- Emoji: 📈 (long) / 📉 (short)
- Includes: qty, entry price, TP, SL

**Trade closes:**
- Emoji: ✅ (win) / ❌ (loss)
- Shows: PnL in $, PnL %, hold time, quantities

**Daily recap:**
- Trade count, win rate, profit factor
- Total PnL and whether it was a halted day

**Circuit breakers:**
- Alert when max consecutive losses triggered
- Reason why trading halted

## Backtest Results Baseline
*Previous 40-day run (tuned params):*
- Profit Factor: 1.18
- Total P&L: +$403
- Win Rate: ~48%
- Symbols: AAPL, TSLA, META

This update should improve consistency through better filtering and risk management.

## Next Steps for Further Optimization

1. **Adaptive TP/SL**: Scale profit targets and stops based on volatility
2. **Breakout filtering**: Skip entries near support/resistance levels
3. **Trade correlation**: Avoid multiple correlated positions
4. **Market regime detection**: Different params for trending vs choppy
5. **Partial take-profits**: Scale out at 50%/75% targets instead of all-or-nothing
6. **Machine learning**: Confidence scoring based on feature importance

## Debugging

Enable detailed logging:
```bash
LOGLEVEL=DEBUG python main.py
```

Check trade journal:
```bash
tail -f trades.jsonl | jq .
```

Query today's trades:
```python
from scalper.analytics import TradeLog
from datetime import date
log = TradeLog("trades.jsonl")
print(log.stats_for_date(date.today()))
```

