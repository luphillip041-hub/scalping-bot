"""Real-time performance metrics and analytics.

Tracks:
- Win/loss ratio
- Profit factor
- Sharpe ratio
- Average win/loss
- Drawdown
- Trade statistics
"""
import json
import logging
from datetime import datetime, date
from dataclasses import dataclass, asdict
from statistics import mean, stdev

log = logging.getLogger("scalper.metrics")


@dataclass
class TradeRecord:
    """Single completed trade."""
    symbol: str
    side: str  # "long" or "short"
    entry_price: float
    exit_price: float
    qty: int
    pnl: float
    pnl_pct: float
    hold_seconds: int
    exit_reason: str  # "tp", "sl", "time"
    entered_at: str  # ISO datetime
    exited_at: str  # ISO datetime
    
    @property
    def is_win(self) -> bool:
        return self.pnl > 0


@dataclass
class DailyMetrics:
    """Daily aggregated metrics."""
    date: str
    total_trades: int
    wins: int
    losses: int
    win_rate: float
    gross_profit: float
    gross_loss: float
    net_pnl: float
    profit_factor: float  # gross_profit / abs(gross_loss)
    avg_win: float
    avg_loss: float
    largest_win: float
    largest_loss: float
    max_drawdown: float
    avg_hold_seconds: int
    sharpe_ratio: float


class MetricsCollector:
    """Collect and analyze trade metrics in real-time."""
    
    def __init__(self, log_file: str = None):
        self.log_file = log_file
        self.trades: list[TradeRecord] = []
        self.daily_trades: dict[date, list[TradeRecord]] = {}
        
    def record_trade(self, trade: TradeRecord):
        """Record a completed trade."""
        self.trades.append(trade)
        
        # Group by date
        trade_date = datetime.fromisoformat(trade.entered_at).date()
        if trade_date not in self.daily_trades:
            self.daily_trades[trade_date] = []
        self.daily_trades[trade_date].append(trade)
        
        # Persist to log file if configured
        if self.log_file:
            try:
                with open(self.log_file, "a") as f:
                    f.write(json.dumps(asdict(trade)) + "\n")
            except Exception as e:
                log.warning("Failed to write metrics: %s", e)
    
    def daily_summary(self, summary_date: date = None) -> DailyMetrics | None:
        """Calculate daily metrics."""
        if summary_date is None:
            summary_date = datetime.now().date()
        
        trades = self.daily_trades.get(summary_date, [])
        if not trades:
            return None
        
        wins = [t for t in trades if t.is_win]
        losses = [t for t in trades if not t.is_win]
        
        total_trades = len(trades)
        win_count = len(wins)
        loss_count = len(losses)
        win_rate = win_count / total_trades if total_trades > 0 else 0.0
        
        # Profit metrics
        gross_profit = sum(t.pnl for t in wins)
        gross_loss = abs(sum(t.pnl for t in losses))
        net_pnl = gross_profit - gross_loss
        profit_factor = gross_profit / gross_loss if gross_loss > 0 else (0.0 if gross_profit == 0 else float('inf'))
        
        # Average win/loss
        avg_win = mean([t.pnl for t in wins]) if wins else 0.0
        avg_loss = mean([t.pnl for t in losses]) if losses else 0.0
        largest_win = max([t.pnl for t in wins]) if wins else 0.0
        largest_loss = min([t.pnl for t in losses]) if losses else 0.0
        
        # Drawdown (peak-to-trough)
        cumulative = 0.0
        peak = 0.0
        max_dd = 0.0
        for trade in trades:
            cumulative += trade.pnl
            if cumulative > peak:
                peak = cumulative
            dd = peak - cumulative
            if dd > max_dd:
                max_dd = dd
        
        # Average hold time
        avg_hold = int(mean([t.hold_seconds for t in trades])) if trades else 0
        
        # Sharpe ratio (simplified: assumes 252 trading days, no risk-free rate)
        pnls = [t.pnl for t in trades]
        sharpe = 0.0
        if len(pnls) > 1:
            daily_return = net_pnl / (win_count + loss_count) if (win_count + loss_count) > 0 else 0
            if daily_return != 0:
                try:
                    volatility = stdev(pnls)
                    if volatility > 0:
                        sharpe = (daily_return / volatility) * (252 ** 0.5)
                except Exception:
                    sharpe = 0.0
        
        return DailyMetrics(
            date=str(summary_date),
            total_trades=total_trades,
            wins=win_count,
            losses=loss_count,
            win_rate=round(win_rate, 4),
            gross_profit=round(gross_profit, 2),
            gross_loss=round(gross_loss, 2),
            net_pnl=round(net_pnl, 2),
            profit_factor=round(profit_factor, 2),
            avg_win=round(avg_win, 2),
            avg_loss=round(avg_loss, 2),
            largest_win=round(largest_win, 2),
            largest_loss=round(largest_loss, 2),
            max_drawdown=round(max_dd, 2),
            avg_hold_seconds=avg_hold,
            sharpe_ratio=round(sharpe, 2),
        )
    
    def all_time_summary(self) -> DailyMetrics | None:
        """Calculate all-time metrics across all trades."""
        if not self.trades:
            return None
        
        wins = [t for t in self.trades if t.is_win]
        losses = [t for t in self.trades if not t.is_win]
        
        total = len(self.trades)
        win_count = len(wins)
        loss_count = len(losses)
        win_rate = win_count / total if total > 0 else 0.0
        
        gross_profit = sum(t.pnl for t in wins)
        gross_loss = abs(sum(t.pnl for t in losses))
        net_pnl = gross_profit - gross_loss
        profit_factor = gross_profit / gross_loss if gross_loss > 0 else (0.0 if gross_profit == 0 else float('inf'))
        
        avg_win = mean([t.pnl for t in wins]) if wins else 0.0
        avg_loss = mean([t.pnl for t in losses]) if losses else 0.0
        largest_win = max([t.pnl for t in wins]) if wins else 0.0
        largest_loss = min([t.pnl for t in losses]) if losses else 0.0
        
        # Drawdown
        cumulative = 0.0
        peak = 0.0
        max_dd = 0.0
        for trade in self.trades:
            cumulative += trade.pnl
            if cumulative > peak:
                peak = cumulative
            dd = peak - cumulative
            if dd > max_dd:
                max_dd = dd
        
        avg_hold = int(mean([t.hold_seconds for t in self.trades])) if self.trades else 0
        
        # Sharpe
        pnls = [t.pnl for t in self.trades]
        sharpe = 0.0
        if len(pnls) > 1:
            daily_return = net_pnl / total if total > 0 else 0
            if daily_return != 0:
                try:
                    volatility = stdev(pnls)
                    if volatility > 0:
                        sharpe = (daily_return / volatility) * (252 ** 0.5)
                except Exception:
                    sharpe = 0.0
        
        return DailyMetrics(
            date="all_time",
            total_trades=total,
            wins=win_count,
            losses=loss_count,
            win_rate=round(win_rate, 4),
            gross_profit=round(gross_profit, 2),
            gross_loss=round(gross_loss, 2),
            net_pnl=round(net_pnl, 2),
            profit_factor=round(profit_factor, 2),
            avg_win=round(avg_win, 2),
            avg_loss=round(avg_loss, 2),
            largest_win=round(largest_win, 2),
            largest_loss=round(largest_loss, 2),
            max_drawdown=round(max_dd, 2),
            avg_hold_seconds=avg_hold,
            sharpe_ratio=round(sharpe, 2),
        )

