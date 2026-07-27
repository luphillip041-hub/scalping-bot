"""Main scalping bot loop (Alpaca, polling-based).

Runs during market hours: pulls recent 1-min bars per symbol, generates
signals, submits bracket orders (entry + take-profit + stop-loss), and
manages time-based exits and the daily-loss circuit breaker.

Features:
- Multi-indicator signal generation (VWAP + volume + momentum + RSI)
- Intelligent position sizing (base + volatility scaling)
- Comprehensive error handling with graceful degradation
- Real-time metrics and performance analytics
- Efficient API usage with caching
"""
import logging
import time
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
from typing import Optional

from alpaca.data.historical import StockHistoricalDataClient
from alpaca.data.requests import StockBarsRequest
from alpaca.data.timeframe import TimeFrame, TimeFrameUnit
from alpaca.data.enums import DataFeed
from alpaca.trading.client import TradingClient
from alpaca.trading.enums import OrderSide, TimeInForce, OrderStatus
from alpaca.trading.requests import MarketOrderRequest, TakeProfitRequest, StopLossRequest

from .config import Config
from .metrics import MetricsCollector, TradeRecord
from .notify import send
from .risk import RiskManager
from .strategy import Side, generate_signal

log = logging.getLogger("scalper")
ET = ZoneInfo("America/New_York")

# Retry config for transient failures
MAX_RETRIES = 3
RETRY_DELAY = 1  # seconds

# Cache for bar data (symbol -> (bars, timestamp))
_bars_cache = {}


class ScalpingBot:
    def __init__(self, cfg: Config):
        cfg.validate()
        self.cfg = cfg
        self.trading = TradingClient(cfg.api_key, cfg.api_secret, paper=cfg.paper)
        self.data = StockHistoricalDataClient(cfg.api_key, cfg.api_secret)
        self.risk = RiskManager(
            max_daily_loss_usd=cfg.max_daily_loss_usd,
            max_trades_per_day=cfg.max_trades_per_day,
            max_positions=cfg.max_positions,
            position_size_usd=cfg.base_position_size_usd,
        )
        self.metrics = MetricsCollector(log_file=cfg.metrics_log_file if cfg.enable_metrics else None)
        
        # Track positions by symbol -> (entry_price, entry_time, order_id, side)
        self.tracked_positions: dict[str, tuple[float, datetime, str, Side]] = {}
        
        # Cache for last synced position state
        self._last_synced_positions = set()
        self._last_position_check = datetime.now(ET)
        
        # Session tracking
        self._session_start_time: Optional[datetime] = None
        self._session_realized_pnl = 0.0

    # ---------- helpers ----------
    def _in_session(self) -> bool:
        """Check if we're within trading hours."""
        now = datetime.now(ET)
        if now.weekday() >= 5:  # weekend
            return False
        t = now.strftime("%H:%M")
        return self.cfg.trade_start <= t <= self.cfg.trade_end

    def _todays_bars(self, symbol: str):
        """Fetch today's bars with caching and retry logic."""
        now = datetime.now(ET)
        
        # Check cache
        if symbol in _bars_cache:
            bars, cache_time = _bars_cache[symbol]
            if (now - cache_time).total_seconds() < self.cfg.bars_cache_ttl_seconds:
                return bars
        
        start = now.replace(hour=9, minute=30, second=0, microsecond=0)
        tf_map = {"1Min": TimeFrame.Minute, "5Min": TimeFrame(5, TimeFrameUnit.Minute)}
        req = StockBarsRequest(
            symbol_or_symbols=symbol,
            timeframe=tf_map.get(self.cfg.bar_timeframe, TimeFrame.Minute),
            start=start,
            end=now,
            feed=DataFeed.IEX,
        )
        
        for attempt in range(MAX_RETRIES):
            try:
                bars = self.data.get_stock_bars(req).data.get(symbol, [])
                bars_list = list(bars)
                # Cache the result
                _bars_cache[symbol] = (bars_list, now)
                return bars_list
            except Exception as e:
                if attempt == MAX_RETRIES - 1:
                    log.error("Failed to fetch bars for %s after %d retries: %s", symbol, MAX_RETRIES, e)
                    return []
                log.debug("bars %s (attempt %d/%d): %s, retrying...", symbol, attempt + 1, MAX_RETRIES, e)
                time.sleep(RETRY_DELAY)
        
        return []

    def _open_positions(self):
        """Fetch all open positions from Alpaca."""
        for attempt in range(MAX_RETRIES):
            try:
                return {p.symbol: p for p in self.trading.get_all_positions()}
            except Exception as e:
                if attempt == MAX_RETRIES - 1:
                    log.error("Failed to fetch positions after %d retries: %s", MAX_RETRIES, e)
                    return {}
                log.debug("open_positions (attempt %d/%d): %s, retrying...", attempt + 1, MAX_RETRIES, e)
                time.sleep(RETRY_DELAY)
        return {}

    def _get_filled_orders(self, after: datetime = None) -> list:
        """Fetch filled orders from Alpaca for PnL tracking."""
        try:
            orders = self.trading.get_orders(limit=100, status=OrderStatus.FILLED)
            if after:
                orders = [o for o in orders if o.filled_at and o.filled_at > after]
            return orders
        except Exception as e:
            log.warning("failed to fetch filled orders: %s", e)
            return []

    def _calculate_position_size(self, price: float, volatility: float = 1.0) -> int:
        """Calculate position size with optional volatility scaling.
        
        Higher volatility → smaller position size for risk management.
        """
        base_qty = self.risk.qty_for(price)
        
        if not self.cfg.position_size_scaling_enabled or volatility <= 0:
            return base_qty
        
        # Scale: volatility > 1.5 reduces size, volatility < 0.7 increases it
        scale = 1.0 / (1.0 + (volatility - 1.0) * 0.5)
        scaled_qty = max(1, int(base_qty * scale))
        
        return scaled_qty

    def _submit_bracket(self, symbol: str, side: Side, confidence: float = 1.0):
        """Submit bracket order with proper TP/SL for long/short.
        
        Args:
            symbol: Stock ticker
            side: LONG or SHORT
            confidence: Signal confidence (0-1) for position sizing
        """
        bars = self._todays_bars(symbol)
        if not bars or len(bars) < 2:
            log.error("No bars available for %s", symbol)
            return
        
        price = bars[-1].close
        
        # Calculate volatility for position sizing
        from .strategy import compute_atr
        atr = compute_atr(bars, period=14)
        atr_pct = (atr / price * 100) if price > 0 else 1.0
        volatility = max(0.5, min(2.0, atr_pct / 1.0))  # normalize to 0.5-2.0 range
        
        # Size: base * confidence * volatility scale
        base_qty = self._calculate_position_size(price, volatility)
        qty = max(1, int(base_qty * confidence))
        
        # Calculate TP and SL based on direction
        if side == Side.LONG:
            tp = price * (1 + self.cfg.take_profit_pct / 100)
            sl = price * (1 - self.cfg.stop_loss_pct / 100)
        else:  # SHORT
            tp = price * (1 - self.cfg.take_profit_pct / 100)
            sl = price * (1 + self.cfg.stop_loss_pct / 100)
        
        order = MarketOrderRequest(
            symbol=symbol,
            qty=qty,
            side=OrderSide.BUY if side == Side.LONG else OrderSide.SELL,
            time_in_force=TimeInForce.DAY,
            take_profit=TakeProfitRequest(limit_price=round(tp, 2)),
            stop_loss=StopLossRequest(stop_price=round(sl, 2)),
        )
        
        try:
            placed_order = self.trading.submit_order(order)
            self.tracked_positions[symbol] = (price, datetime.now(ET), placed_order.id, side)
            self.risk.register_trade(datetime.now(ET).date())
            log.info(
                "ENTER %s %s x%d @ %.2f (tp %.2f / sl %.2f) conf=%.2f vol=%.2f",
                side.value, symbol, qty, price, tp, sl, confidence, volatility
            )
            send(
                f"{'📈' if side == Side.LONG else '📉'} Entered {side.value.upper()} {symbol}",
                f"Bracket order submitted",
                "entry_long" if side == Side.LONG else "entry_short",
                {
                    "Qty": qty,
                    "Entry": f"${price:.2f}",
                    "TP": f"${tp:.2f}",
                    "SL": f"${sl:.2f}",
                    "Confidence": f"{confidence:.1%}",
                    "Volatility": f"{volatility:.2f}x"
                },
            )
        except Exception as e:
            log.error("Failed to submit bracket for %s: %s", symbol, e)
            send(
                "⚠️ Order submission failed",
                f"Could not enter {symbol}: {str(e)}",
                "info",
                {"Symbol": symbol, "Error": str(e)},
            )

    def _check_time_exits(self, positions):
        """Close positions that have exceeded max hold time."""
        now = datetime.now(ET)
        for sym in list(self.tracked_positions.keys()):
            entry_price, entered_at, order_id, side = self.tracked_positions[sym]
            hold_time = (now - entered_at)
            max_hold = timedelta(minutes=self.cfg.max_hold_minutes)
            
            if hold_time > max_hold:
                if sym in positions:
                    log.info("TIME EXIT %s after %d min (max=%d)", sym, hold_time.total_seconds() / 60, self.cfg.max_hold_minutes)
                    try:
                        self.trading.close_position(sym)
                        exit_price = positions[sym].current_price
                        qty = self._calculate_position_size(entry_price)
                        pnl = (exit_price - entry_price) * qty
                        self.risk.record_close(pnl, datetime.now(ET).date())
                        
                        # Record trade metrics
                        self.metrics.record_trade(TradeRecord(
                            symbol=sym,
                            side=side.value,
                            entry_price=entry_price,
                            exit_price=exit_price,
                            qty=qty,
                            pnl=pnl,
                            pnl_pct=(exit_price - entry_price) / entry_price * 100,
                            hold_seconds=int(hold_time.total_seconds()),
                            exit_reason="time",
                            entered_at=entered_at.isoformat(),
                            exited_at=now.isoformat(),
                        ))
                        
                        send(
                            "⏱️ Time-based exit",
                            f"Closed {sym} after {self.cfg.max_hold_minutes} minutes",
                            "exit_win" if pnl > 0 else "exit_loss",
                            {
                                "Exit Price": f"${exit_price:.2f}",
                                "PnL": f"${pnl:+.2f}",
                                "Hold Time": f"{int(hold_time.total_seconds() / 60)}m"
                            },
                        )
                    except Exception as e:
                        log.error("Failed to close position %s: %s", sym, e)
                
                self.tracked_positions.pop(sym, None)

    def _sync_positions(self, positions):
        """Detect and record closed positions; clean up stale tracking.
        
        When a position closes via TP/SL (bracket legs), we detect it here
        and record metrics.
        """
        current_syms = set(positions.keys())
        
        for sym in list(self.tracked_positions.keys()):
            if sym not in current_syms and sym not in self._last_synced_positions:
                # Position just closed
                entry_price, entered_at, order_id, side = self.tracked_positions[sym]
                qty = self._calculate_position_size(entry_price)
                exit_price = entry_price
                exit_reason = "bracket_exit"
                now = datetime.now(ET)
                hold_time = (now - entered_at)
                
                try:
                    orders = self.trading.get_orders(limit=50, status=OrderStatus.FILLED)
                    for o in orders:
                        if o.symbol == sym and o.id != order_id and o.filled_at:
                            exit_price = o.filled_avg_price
                            # Determine if it was TP or SL
                            if o.order_class == "bracket":
                                exit_reason = "tp" if (
                                    (side == Side.LONG and o.filled_avg_price > entry_price) or
                                    (side == Side.SHORT and o.filled_avg_price < entry_price)
                                ) else "sl"
                            break
                except Exception as e:
                    log.debug("Error fetching exit price for %s: %s", sym, e)
                
                pnl = (exit_price - entry_price) * qty if side == Side.LONG else (entry_price - exit_price) * qty
                self.risk.record_close(pnl, datetime.now(ET).date())
                
                # Record trade metrics
                self.metrics.record_trade(TradeRecord(
                    symbol=sym,
                    side=side.value,
                    entry_price=entry_price,
                    exit_price=exit_price,
                    qty=qty,
                    pnl=pnl,
                    pnl_pct=(exit_price - entry_price) / entry_price * 100 if side == Side.LONG else (entry_price - exit_price) / entry_price * 100,
                    hold_seconds=int(hold_time.total_seconds()),
                    exit_reason=exit_reason,
                    entered_at=entered_at.isoformat(),
                    exited_at=now.isoformat(),
                ))
                
                log.info("CLOSE %s via %s pnl=$%.2f (daily $%.2f)", sym, exit_reason, pnl, self.risk.daily_pnl)
                
                send(
                    f"{'✅' if pnl > 0 else '❌'} Exit {sym.upper()} ({exit_reason})",
                    f"Position closed",
                    "exit_win" if pnl > 0 else "exit_loss",
                    {
                        "Exit": f"${exit_price:.2f}",
                        "PnL": f"${pnl:+.2f}",
                        "PnL %": f"{(pnl / (entry_price * qty) * 100):+.2f}%",
                        "Reason": exit_reason.upper()
                    },
                )
                
                self.tracked_positions.pop(sym, None)
            elif sym not in current_syms:
                self.tracked_positions.pop(sym, None)
        
        self._last_synced_positions = current_syms

    def _log_session_metrics(self):
        """Log daily performance metrics."""
        if not self.cfg.enable_metrics:
            return
        
        metrics = self.metrics.daily_summary()
        if not metrics:
            return
        
        log.info(
            "DAILY METRICS | trades=%d | w/l=%d/%d (%.1f%%) | pnl=$%.2f | "
            "pf=%.2f | avg_w/l=$%.2f/$%.2f | max_dd=$%.2f | sharpe=%.2f",
            metrics.total_trades,
            metrics.wins,
            metrics.losses,
            metrics.win_rate * 100,
            metrics.net_pnl,
            metrics.profit_factor,
            metrics.avg_win,
            metrics.avg_loss,
            metrics.max_drawdown,
            metrics.sharpe_ratio,
        )

    # ---------- main loop ----------
    def run(self):
        """Main bot loop."""
        try:
            acct = self.trading.get_account()
            log.info(
                "Connected. Account %s | equity $%.2f | paper=%s | symbols=%d",
                acct.account_number, float(acct.equity), self.cfg.paper, len(self.cfg.symbols)
            )
        except Exception as e:
            log.error("Failed to connect to Alpaca: %s", e)
            raise
        
        was_in_session = False
        
        while True:
            try:
                if not self._in_session():
                    if was_in_session:
                        # Session just ended → log daily recap
                        self._log_session_metrics()
                        send(
                            "📋 Daily recap",
                            f"Session over. Check logs for full metrics.",
                            "exit_win" if self.risk.daily_pnl >= 0 else "exit_loss",
                            {
                                "Trades": self.risk._trade_count,
                                "Net PnL": f"${self.risk.daily_pnl:+.2f}",
                                "Halted": "Yes" if self.risk.halted else "No"
                            }
                        )
                        was_in_session = False
                    
                    log.debug(
                        "Outside session %s–%s ET. Sleeping %ds.",
                        self.cfg.trade_start, self.cfg.trade_end, self.cfg.poll_seconds * 4
                    )
                    time.sleep(self.cfg.poll_seconds * 4)
                    continue
                
                if not was_in_session:
                    was_in_session = True
                    self._session_start_time = datetime.now(ET)
                    send(
                        "🟢 Trading session started",
                        f"Session active {self.cfg.trade_start}–{self.cfg.trade_end} ET",
                        "info",
                        {"Symbols": len(self.cfg.symbols)}
                    )

                # Fetch positions (with graceful degradation)
                try:
                    positions = self._open_positions()
                except Exception as e:
                    log.error("Failed to fetch positions, skipping cycle: %s", e)
                    time.sleep(self.cfg.poll_seconds)
                    continue

                self._sync_positions(positions)
                self._check_time_exits(positions)

                today = datetime.now(ET).date()
                ok, why = self.risk.can_trade(len(self.tracked_positions), today)
                if not ok:
                    log.warning("Cannot trade: %s", why)
                    time.sleep(self.cfg.poll_seconds)
                    continue

                # Process each symbol
                for sym in self.cfg.symbols:
                    if sym in positions or sym in self.tracked_positions:
                        continue
                    
                    try:
                        bars = self._todays_bars(sym)
                    except Exception as e:
                        log.debug("Failed to fetch bars for %s: %s", sym, e)
                        continue
                    
                    if len(bars) < self.cfg.min_bars_for_entry:
                        continue
                    
                    sig = generate_signal(bars, self.cfg)
                    if sig:
                        log.info("SIGNAL %s %s (conf=%.2f) — %s", sym, sig.side.value, sig.confidence, sig.reason)
                        try:
                            self._submit_bracket(sym, sig.side, confidence=sig.confidence)
                            # Re-check if we can trade after this order
                            ok, why = self.risk.can_trade(len(self.tracked_positions), today)
                            if not ok:
                                log.info("Stopping signal processing: %s", why)
                                break
                        except Exception as e:
                            log.error("Failed to submit order for %s: %s", sym, e)

                time.sleep(self.cfg.poll_seconds)
                
            except KeyboardInterrupt:
                log.info("Stopped by user.")
                self._log_session_metrics()
                break
            except Exception as e:
                log.exception("Loop error: %s", e)
                time.sleep(self.cfg.poll_seconds)

