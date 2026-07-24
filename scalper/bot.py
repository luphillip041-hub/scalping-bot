"""Main scalping bot loop (Alpaca, polling-based).

Runs during market hours: pulls recent 1-min bars per symbol, generates
signals, submits bracket orders (entry + take-profit + stop-loss), and
manages time-based exits and the daily-loss circuit breaker.
"""
import logging
import time
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
from collections import deque

from alpaca.data.historical import StockHistoricalDataClient
from alpaca.data.requests import StockBarsRequest
from alpaca.data.timeframe import TimeFrame, TimeFrameUnit
from alpaca.data.enums import DataFeed
from alpaca.trading.client import TradingClient
from alpaca.trading.enums import OrderSide, TimeInForce
from alpaca.trading.requests import MarketOrderRequest, TakeProfitRequest, StopLossRequest

from .config import Config
from .notify import send
from .risk import RiskManager
from .strategy import Side, generate_signal

log = logging.getLogger("scalper")
ET = ZoneInfo("America/New_York")


class TradeRecord:
    """Track trade entry/exit and compute exact PnL."""
    def __init__(self, symbol: str, side: Side, entry_price: float, qty: int, 
                 entry_time: datetime, tp: float, sl: float):
        self.symbol = symbol
        self.side = side
        self.entry_price = entry_price
        self.qty = qty
        self.entry_time = entry_time
        self.tp = tp
        self.sl = sl
        self.exit_price = None
        self.exit_time = None
        self.close_reason = None

    def close(self, exit_price: float, reason: str):
        self.exit_price = exit_price
        self.exit_time = datetime.now(ET)
        self.close_reason = reason

    @property
    def pnl(self) -> float:
        if not self.exit_price:
            return 0.0
        gross = (self.exit_price - self.entry_price) * self.qty
        return gross if self.side == Side.LONG else -gross

    @property
    def pnl_pct(self) -> float:
        if self.entry_price == 0:
            return 0.0
        return (self.pnl / (self.entry_price * self.qty)) * 100

    @property
    def hold_time_sec(self) -> int:
        end = self.exit_time or datetime.now(ET)
        return int((end - self.entry_time).total_seconds())


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
        
        # Trade tracking
        self.active_trades: dict[str, TradeRecord] = {}
        self.closed_trades: deque = deque(maxlen=100)  # last 100 trades
        
        # Performance metrics
        self.consecutive_losses = 0
        self.max_consecutive_losses = 3
        
        # Signal rate limiting: cooldown per symbol after false signal
        self.signal_cooldown: dict[str, datetime] = {}
        self.cooldown_seconds = 60

    # ---------- helpers ----------
    def _in_session(self) -> bool:
        now = datetime.now(ET)
        if now.weekday() >= 5:
            return False
        t = now.strftime("%H:%M")
        return self.cfg.trade_start <= t <= self.cfg.trade_end

    def _todays_bars(self, symbol: str, limit: int = 1000):
        """Fetch up to limit bars from today's session."""
        now = datetime.now(ET)
        start = now.replace(hour=9, minute=30, second=0, microsecond=0)
        
        tf_map = {
            "1Min": TimeFrame.Minute,
            "5Min": TimeFrame(5, TimeFrameUnit.Minute),
        }
        req = StockBarsRequest(
            symbol_or_symbols=symbol,
            timeframe=tf_map.get(self.cfg.bar_timeframe, TimeFrame.Minute),
            start=start,
            end=now,
            feed=DataFeed.IEX,
            limit=limit,
        )
        bars = self.data.get_stock_bars(req).data.get(symbol, [])
        return list(bars)

    def _open_positions(self):
        """Get map of symbol -> position."""
        return {p.symbol: p for p in self.trading.get_all_positions()}

    def _get_volatility(self, bars) -> float:
        """Estimate intraday volatility (ATR-like, pct of close)."""
        if len(bars) < 10:
            return 0.5  # default to 0.5% if not enough bars
        
        closes = [b.close for b in bars[-20:]]
        returns = [
            (closes[i] - closes[i-1]) / closes[i-1] * 100
            for i in range(1, len(closes))
        ]
        variance = sum(r**2 for r in returns) / len(returns)
        return (variance ** 0.5) if variance > 0 else 0.5

    def _position_size_for(self, price: float, bars) -> int:
        """Risk-adjusted position size based on volatility."""
        vol = self._get_volatility(bars)
        # higher vol -> smaller position
        vol_factor = max(0.5, 1.0 - (vol / 10))
        return max(1, int((self.cfg.position_size_usd * vol_factor) // price))

    def _can_signal(self, symbol: str) -> bool:
        """Check if symbol is in signal cooldown (post-false-signal)."""
        last = self.signal_cooldown.get(symbol)
        if not last:
            return True
        elapsed = (datetime.now(ET) - last).total_seconds()
        return elapsed > self.cooldown_seconds

    def _metrics_summary(self) -> dict:
        """Compute performance metrics from closed trades."""
        if not self.closed_trades:
            return {}
        
        wins = sum(1 for t in self.closed_trades if t.pnl > 0)
        losses = sum(1 for t in self.closed_trades if t.pnl < 0)
        total_pnl = sum(t.pnl for t in self.closed_trades)
        
        win_rate = (wins / len(self.closed_trades) * 100) if self.closed_trades else 0
        avg_hold = sum(t.hold_time_sec for t in self.closed_trades) / len(self.closed_trades)
        
        return {
            "trades": len(self.closed_trades),
            "wins": wins,
            "losses": losses,
            "win_rate": f"{win_rate:.1f}%",
            "total_pnl": f"${total_pnl:+.2f}",
            "avg_hold_sec": int(avg_hold),
        }

    # ---------- order handling ----------
    def _submit_bracket(self, symbol: str, side: Side, bars):
        """Submit bracket order with risk-adjusted sizing."""
        price = bars[-1].close
        qty = self._position_size_for(price, bars)
        
        tp = price * (1 + self.cfg.take_profit_pct / 100) if side == Side.LONG else price * (1 - self.cfg.take_profit_pct / 100)
        sl = price * (1 - self.cfg.stop_loss_pct / 100) if side == Side.LONG else price * (1 + self.cfg.stop_loss_pct / 100)
        
        order = MarketOrderRequest(
            symbol=symbol,
            qty=qty,
            side=OrderSide.BUY if side == Side.LONG else OrderSide.SELL,
            time_in_force=TimeInForce.DAY,
            take_profit=TakeProfitRequest(limit_price=round(tp, 2)),
            stop_loss=StopLossRequest(stop_price=round(sl, 2)),
        )
        
        self.trading.submit_order(order)
        
        # Record trade
        trade = TradeRecord(symbol, side, price, qty, datetime.now(ET), tp, sl)
        self.active_trades[symbol] = trade
        self.risk.register_trade(datetime.now(ET).date())
        
        log.info(
            "ENTER %s %s x%d @ %.2f (tp %.2f / sl %.2f) [vol-adj]",
            side.value, symbol, qty, price, tp, sl
        )
        send(
            f"{'📈' if side == Side.LONG else '📉'} {side.value.upper()} {symbol}",
            f"Bracket order submitted",
            "entry_long" if side == Side.LONG else "entry_short",
            {
                "Qty": qty,
                "Entry": f"${price:.2f}",
                "TP": f"${tp:.2f}",
                "SL": f"${sl:.2f}",
            },
        )

    def _check_time_exits(self, positions):
        """Exit positions that have exceeded max hold time."""
        now = datetime.now(ET)
        for sym in list(self.active_trades.keys()):
            trade = self.active_trades[sym]
            hold_min = (now - trade.entry_time).total_seconds() / 60
            
            if hold_min > self.cfg.max_hold_minutes:
                log.info("TIME EXIT %s after %.1f min", sym, hold_min)
                try:
                    if sym in positions:
                        self.trading.close_position(sym)
                    else:
                        # position already closed by bracket
                        pass
                except Exception as e:
                    log.error("close %s: %s", sym, e)

    def _sync_closed(self, positions):
        """Detect positions closed by bracket legs and record PnL."""
        for sym in list(self.active_trades.keys()):
            if sym not in positions and self.active_trades[sym].exit_price is None:
                try:
                    bars = self._todays_bars(sym)
                    px = bars[-1].close if bars else self.active_trades[sym].entry_price
                except Exception as e:
                    log.error("fetch price %s: %s", sym, e)
                    px = self.active_trades[sym].entry_price
                
                trade = self.active_trades[sym]
                trade.close(px, "bracket_exit")
                self.closed_trades.append(trade)
                self.risk.record_close(trade.pnl, datetime.now(ET).date())
                
                emoji = "✅" if trade.pnl > 0 else "❌"
                log.info(
                    "CLOSED %s: pnl=$%.2f (%.2f%%) hold=%ds [%s]",
                    sym, trade.pnl, trade.pnl_pct, trade.hold_time_sec,
                    trade.close_reason
                )
                
                # Update consecutive loss counter
                if trade.pnl <= 0:
                    self.consecutive_losses += 1
                else:
                    self.consecutive_losses = 0
                
                del self.active_trades[sym]
                
                send(
                    f"{emoji} CLOSED {sym}",
                    f"P&L: ${trade.pnl:+.2f} ({trade.pnl_pct:+.2f}%)",
                    "exit_win" if trade.pnl > 0 else "exit_loss",
                    {
                        "Entry": f"${trade.entry_price:.2f}",
                        "Exit": f"${trade.exit_price:.2f}",
                        "Hold": f"{trade.hold_time_sec}s",
                        "Qty": trade.qty,
                    },
                )

    # ---------- main loop ----------
    def run(self):
        acct = self.trading.get_account()
        log.info(
            "Connected. Account %s | equity $%s | paper=%s",
            acct.account_number, acct.equity, self.cfg.paper,
        )
        was_in_session = False
        
        while True:
            try:
                if not self._in_session():
                    if was_in_session:
                        # session just ended → daily recap
                        metrics = self._metrics_summary()
                        log.info("Session end. Metrics: %s", metrics)
                        send(
                            "📋 Daily recap",
                            f"Realized P&L: ${self.risk.daily_pnl:+.2f}",
                            "exit_win" if self.risk.daily_pnl > 0 else "exit_loss",
                            {
                                "Trades": self.risk._trade_count,
                                "Halted": "Yes" if self.risk.halted else "No",
                                "Win rate": metrics.get("win_rate", "N/A"),
                                "Total closed P&L": metrics.get("total_pnl", "N/A"),
                            },
                        )
                        was_in_session = False
                    
                    log.info(
                        "Outside session %s–%s ET. Sleeping 60s.",
                        self.cfg.trade_start, self.cfg.trade_end,
                    )
                    time.sleep(60)
                    continue
                
                was_in_session = True

                # Sync closed positions and check time exits
                positions = self._open_positions()
                self._sync_closed(positions)
                self._check_time_exits(positions)

                today = datetime.now(ET).date()
                own = [s for s in positions if s in self.cfg.symbols]
                ok, why = self.risk.can_trade(len(own), today)
                
                # Stop trading after max consecutive losses
                if self.consecutive_losses >= self.max_consecutive_losses:
                    log.warning(
                        "Max consecutive losses (%d) reached. Halting signals.",
                        self.consecutive_losses,
                    )
                    send(
                        "⚠️ Circuit breaker: max consecutive losses",
                        f"{self.consecutive_losses} losses in a row. Stopping signals.",
                        "halt",
                        {"Reset after": "next session"},
                    )
                    ok = False
                    why = f"circuit: {self.consecutive_losses} consecutive losses"
                
                if not ok:
                    log.warning("No new trades: %s", why)
                    time.sleep(self.cfg.poll_seconds)
                    continue

                # Scan for signals
                for sym in self.cfg.symbols:
                    if sym in positions or sym in self.active_trades:
                        continue
                    if not self._can_signal(sym):
                        continue
                    
                    try:
                        bars = self._todays_bars(sym)
                    except Exception as e:
                        log.error("bars %s: %s", sym, e)
                        continue
                    
                    if len(bars) < 10:
                        continue
                    
                    sig = generate_signal(bars, self.cfg)
                    if sig:
                        log.info("SIGNAL %s %s — %s", sym, sig.side.value, sig.reason)
                        try:
                            self._submit_bracket(sym, sig.side, bars)
                            ok, why = self.risk.can_trade(
                                len(self._open_positions()), today
                            )
                            if not ok:
                                break
                        except Exception as e:
                            log.error("order %s: %s", sym, e)
                            # Add to cooldown on order failure
                            self.signal_cooldown[sym] = datetime.now(ET)

                time.sleep(self.cfg.poll_seconds)
                
            except KeyboardInterrupt:
                log.info("Stopped by user.")
                break
            except Exception as e:
                log.exception("loop error: %s", e)
                time.sleep(self.cfg.poll_seconds)

