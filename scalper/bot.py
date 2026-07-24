"""Main scalping bot loop (Alpaca, polling-based).

Runs during market hours: pulls recent 1-min bars per symbol, generates
signals, submits bracket orders (entry + take-profit + stop-loss), and
manages time-based exits and the daily-loss circuit breaker.
"""
import logging
import time
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from alpaca.data.historical import StockHistoricalDataClient
from alpaca.data.requests import StockBarsRequest
from alpaca.data.timeframe import TimeFrame, TimeFrameUnit
from alpaca.data.enums import DataFeed
from alpaca.trading.client import TradingClient
from alpaca.trading.enums import OrderSide, TimeInForce, OrderStatus
from alpaca.trading.requests import MarketOrderRequest, TakeProfitRequest, StopLossRequest

from .config import Config
from .notify import send
from .risk import RiskManager
from .strategy import Side, generate_signal

log = logging.getLogger("scalper")
ET = ZoneInfo("America/New_York")

# Retry config for transient failures
MAX_RETRIES = 3
RETRY_DELAY = 1  # seconds


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
            position_size_usd=cfg.position_size_usd,
        )
        # Track positions by symbol -> (entry_price, entry_time, order_id)
        self.tracked_positions: dict[str, tuple[float, datetime, str]] = {}
        # Cache for last synced position state
        self._last_synced_positions = set()

    # ---------- helpers ----------
    def _in_session(self) -> bool:
        now = datetime.now(ET)
        if now.weekday() >= 5:
            return False
        t = now.strftime("%H:%M")
        return self.cfg.trade_start <= t <= self.cfg.trade_end

    def _todays_bars(self, symbol: str):
        """Fetch today's bars with retry logic."""
        now = datetime.now(ET)
        start = now.replace(hour=9, minute=30, second=0, microsecond=0)
        tf_map = {"1Min": TimeFrame.Minute, "5Min": TimeFrame(5, TimeFrameUnit.Minute)}
        req = StockBarsRequest(
            symbol_or_symbols=symbol,
            timeframe=tf_map.get(self.cfg.bar_timeframe, TimeFrame.Minute),
            start=start,
            end=now,
            feed=DataFeed.IEX,  # free plan: SIP blocks the last 15 min
        )
        for attempt in range(MAX_RETRIES):
            try:
                bars = self.data.get_stock_bars(req).data.get(symbol, [])
                return list(bars)
            except Exception as e:
                if attempt == MAX_RETRIES - 1:
                    raise
                log.warning("bars %s (attempt %d/%d): %s, retrying...", symbol, attempt + 1, MAX_RETRIES, e)
                time.sleep(RETRY_DELAY)
        return []

    def _open_positions(self):
        """Fetch all open positions from Alpaca."""
        for attempt in range(MAX_RETRIES):
            try:
                return {p.symbol: p for p in self.trading.get_all_positions()}
            except Exception as e:
                if attempt == MAX_RETRIES - 1:
                    raise
                log.warning("open_positions (attempt %d/%d): %s, retrying...", attempt + 1, MAX_RETRIES, e)
                time.sleep(RETRY_DELAY)
        return {}

    def _get_filled_orders(self, after: datetime = None) -> list:
        """Fetch filled orders from Alpaca for PnL tracking."""
        try:
            # Get orders from today or since a specific time
            limit = 100
            orders = self.trading.get_orders(limit=limit, status=OrderStatus.FILLED)
            if after:
                orders = [o for o in orders if o.filled_at and o.filled_at > after]
            return orders
        except Exception as e:
            log.warning("failed to fetch filled orders: %s", e)
            return []

    def _calculate_realized_pnl(self) -> float:
        """Calculate realized PnL from today's filled orders (sell - buy pairs)."""
        today = datetime.now(ET).date()
        today_start = datetime.now(ET).replace(hour=0, minute=0, second=0, microsecond=0)
        
        orders = self._get_filled_orders(after=today_start)
        pnl = 0.0
        
        # Group by symbol and track entry/exit
        symbol_fills = {}
        for o in orders:
            if not o.filled_at or o.filled_at.date() != today:
                continue
            sym = o.symbol
            if sym not in symbol_fills:
                symbol_fills[sym] = []
            symbol_fills[sym].append(o)
        
        # Calculate PnL for each symbol's trades
        for sym, fills in symbol_fills.items():
            # Sort by fill time
            fills.sort(key=lambda x: x.filled_at)
            buy_qty = 0
            buy_cost = 0.0
            for fill in fills:
                if fill.side == OrderSide.BUY:
                    buy_qty += fill.qty
                    buy_cost += fill.qty * fill.filled_avg_price
                elif fill.side == OrderSide.SELL:
                    if buy_qty > 0:
                        pnl += fill.qty * (fill.filled_avg_price - buy_cost / buy_qty)
                        buy_qty -= fill.qty
                        if buy_qty <= 0:
                            buy_qty = 0
                            buy_cost = 0.0
        
        return pnl

    # ---------- order handling ----------
    def _submit_bracket(self, symbol: str, side: Side):
        """Submit bracket order with proper TP/SL for long/short."""
        bars = self._todays_bars(symbol)
        if not bars:
            log.error("No bars available for %s", symbol)
            return
        
        price = bars[-1].close
        qty = self.risk.qty_for(price)
        
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
            self.tracked_positions[symbol] = (price, datetime.now(ET), placed_order.id)
            self.risk.register_trade(datetime.now(ET).date())
            log.info("ENTER %s %s x%d @ %.2f (tp %.2f / sl %.2f)", side.value, symbol, qty, price, tp, sl)
            send(
                f"{'📈' if side == Side.LONG else '📉'} Entered {side.value.upper()} {symbol}",
                f"Bracket order submitted",
                "entry_long" if side == Side.LONG else "entry_short",
                {"Qty": qty, "Entry": f"${price:.2f}", "TP": f"${tp:.2f}", "SL": f"${sl:.2f}"},
            )
        except Exception as e:
            log.error("Failed to submit bracket for %s: %s", symbol, e)
            send(
                "⚠️ Order submission failed",
                f"Could not enter {symbol}: {str(e)}",
                "info",
                {"Symbol": symbol},
            )

    def _check_time_exits(self, positions):
        """Close positions that have exceeded max hold time."""
        now = datetime.now(ET)
        for sym in list(self.tracked_positions.keys()):
            entry_price, entered_at, order_id = self.tracked_positions[sym]
            if (now - entered_at) > timedelta(minutes=self.cfg.max_hold_minutes):
                if sym in positions:
                    log.info("TIME EXIT %s after %d min", sym, self.cfg.max_hold_minutes)
                    try:
                        self.trading.close_position(sym)
                        exit_price = positions[sym].current_price
                        send(
                            "⏱️ Time-based exit",
                            f"Closed {sym} after {self.cfg.max_hold_minutes} minutes",
                            "exit_win",
                            {"Exit Price": f"${exit_price:.2f}", "Hold Time": f"{self.cfg.max_hold_minutes}m"},
                        )
                    except Exception as e:
                        log.error("Failed to close position %s: %s", sym, e)
                self.tracked_positions.pop(sym, None)

    def _record_closed_position(self, symbol: str, positions: dict):
        """Record PnL for a closed position and clean up tracking."""
        if symbol not in self.tracked_positions:
            return
        
        entry_price, entered_at, order_id = self.tracked_positions[symbol]
        qty = self.risk.qty_for(entry_price)
        
        # Try to get actual exit price from recent orders
        exit_price = entry_price
        try:
            orders = self.trading.get_orders(limit=50, status=OrderStatus.FILLED)
            for o in orders:
                if o.symbol == symbol and o.id != order_id and o.filled_at:
                    exit_price = o.filled_avg_price
                    break
        except Exception:
            pass
        
        pnl = (exit_price - entry_price) * qty
        self.risk.record_close(pnl, datetime.now(ET).date())
        log.info("CLOSE %s pnl=$%.2f (daily $%.2f)", symbol, pnl, self.risk.daily_pnl)
        self.tracked_positions.pop(symbol, None)

    def _sync_positions(self, positions):
        """Detect and record closed positions; clean up stale tracking."""
        current_syms = set(positions.keys())
        
        # Find positions we tracked that are now closed
        for sym in list(self.tracked_positions.keys()):
            if sym not in current_syms and sym not in self._last_synced_positions:
                # Position just closed
                self._record_closed_position(sym, positions)
            elif sym not in current_syms:
                # Already detected; clean up
                self.tracked_positions.pop(sym, None)
        
        self._last_synced_positions = current_syms

    # ---------- main loop ----------
    def run(self):
        acct = self.trading.get_account()
        log.info("Connected. Account %s | equity $%s | paper=%s",
                 acct.account_number, acct.equity, self.cfg.paper)
        was_in_session = False
        while True:
            try:
                if not self._in_session():
                    if was_in_session:
                        # session just ended → daily recap
                        send("📋 Daily recap",
                             f"Session over. Realized P&L: ${self.risk.daily_pnl:+.2f}",
                             "exit_win" if self.risk.daily_pnl > 0 else "exit_loss",
                             {"Trades": self.risk._trade_count,
                              "Halted by loss limit": self.risk.halted})
                        was_in_session = False
                    log.info("Outside session %s–%s ET. Sleeping 60s.",
                             self.cfg.trade_start, self.cfg.trade_end)
                    time.sleep(60)
                    continue
                was_in_session = True

                try:
                    positions = self._open_positions()
                except Exception as e:
                    log.error("Failed to fetch positions: %s", e)
                    time.sleep(self.cfg.poll_seconds)
                    continue

                self._sync_positions(positions)
                self._check_time_exits(positions)

                today = datetime.now(ET).date()
                # only count tracked positions
                ok, why = self.risk.can_trade(len(self.tracked_positions), today)
                if not ok:
                    log.warning("No new trades: %s", why)
                    time.sleep(self.cfg.poll_seconds)
                    continue

                for sym in self.cfg.symbols:
                    if sym in positions or sym in self.tracked_positions:
                        continue
                    
                    try:
                        bars = self._todays_bars(sym)
                    except Exception as e:
                        log.error("Failed to fetch bars for %s: %s", sym, e)
                        continue
                    
                    # Validate we have enough bars for strategy
                    if len(bars) < 10:
                        continue
                    
                    sig = generate_signal(bars, self.cfg)
                    if sig:
                        log.info("SIGNAL %s %s — %s", sym, sig.side.value, sig.reason)
                        try:
                            self._submit_bracket(sym, sig.side)
                            # Re-check if we can trade after this order
                            ok, why = self.risk.can_trade(len(self.tracked_positions), today)
                            if not ok:
                                break
                        except Exception as e:
                            log.error("Failed to submit order for %s: %s", sym, e)

                time.sleep(self.cfg.poll_seconds)
            except KeyboardInterrupt:
                log.info("Stopped by user.")
                break
            except Exception as e:
                log.exception("Loop error: %s", e)
                time.sleep(self.cfg.poll_seconds)

