"""Risk management: position sizing, daily loss circuit breaker, trade limits."""
from dataclasses import dataclass, field
from datetime import date
import math


@dataclass
class RiskManager:
    max_daily_loss_usd: float
    max_trades_per_day: int
    max_positions: int
    position_size_usd: float
    _day: date | None = None
    _realized_pnl: float = 0.0
    _trade_count: int = 0
    _halted: bool = False
    
    # Performance tracking (per session)
    _session_trades: list = field(default_factory=list)  # [(pnl, is_win), ...]
    _max_draw: float = 0.0
    _peak_pnl: float = 0.0

    def _roll_day(self, today: date):
        """Reset daily counters if date changed."""
        if self._day != today:
            self._day = today
            self._realized_pnl = 0.0
            self._trade_count = 0
            self._halted = False
            self._session_trades.clear()
            self._max_draw = 0.0
            self._peak_pnl = 0.0

    def record_close(self, pnl: float, today: date):
        """Record a closed trade and check circuit breaker."""
        self._roll_day(today)
        self._realized_pnl += pnl
        self._trade_count += 1
        self._session_trades.append((pnl, pnl > 0))
        
        # Track peak and drawdown
        if self._realized_pnl > self._peak_pnl:
            self._peak_pnl = self._realized_pnl
        
        drawdown = self._peak_pnl - self._realized_pnl
        if drawdown > self._max_draw:
            self._max_draw = drawdown
        
        # Circuit breaker: halt on max daily loss
        if self._realized_pnl <= -abs(self.max_daily_loss_usd):
            self._halted = True

    def can_trade(self, open_positions: int, today: date) -> tuple[bool, str]:
        """Check if new trades are allowed."""
        self._roll_day(today)
        if self._halted:
            return False, f"halted: daily loss ${self._realized_pnl:.2f}"
        if self._trade_count >= self.max_trades_per_day:
            return False, "halted: max trades/day reached"
        if open_positions >= self.max_positions:
            return False, "halted: max positions reached"
        return True, "ok"

    def register_trade(self, today: date):
        """Register a new trade entry."""
        self._roll_day(today)
        self._trade_count += 1

    def qty_for(self, price: float) -> int:
        """Compute share quantity for given price."""
        return max(1, int(self.position_size_usd // price))

    def win_rate(self) -> float:
        """Compute win rate % from closed trades this session."""
        if not self._session_trades:
            return 0.0
        wins = sum(1 for _, is_win in self._session_trades if is_win)
        return (wins / len(self._session_trades)) * 100

    def profit_factor(self) -> float:
        """Compute profit factor (gross wins / gross losses)."""
        gross_wins = sum(pnl for pnl, _ in self._session_trades if pnl > 0)
        gross_loss = abs(sum(pnl for pnl, _ in self._session_trades if pnl < 0))
        
        if gross_loss == 0:
            return float('inf') if gross_wins > 0 else 0.0
        return gross_wins / gross_loss

    @property
    def daily_pnl(self) -> float:
        return self._realized_pnl

    @property
    def halted(self) -> bool:
        return self._halted

    def session_summary(self) -> dict:
        """Return summary metrics for the session."""
        return {
            "trades": len(self._session_trades),
            "wins": sum(1 for _, w in self._session_trades if w),
            "losses": sum(1 for _, w in self._session_trades if not w),
            "win_rate": f"{self.win_rate():.1f}%",
            "profit_factor": f"{self.profit_factor():.2f}",
            "pnl": f"${self._realized_pnl:+.2f}",
            "max_drawdown": f"${self._max_draw:.2f}",
        }

