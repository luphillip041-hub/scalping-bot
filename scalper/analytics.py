"""Trade analytics and performance tracking."""
import json
from pathlib import Path
from datetime import datetime, date
from zoneinfo import ZoneInfo
from typing import Optional

ET = ZoneInfo("America/New_York")


class TradeLog:
    """Persistent trade journal."""
    
    def __init__(self, log_file: str = "trades.jsonl"):
        self.log_file = Path(log_file)
        self.log_file.parent.mkdir(parents=True, exist_ok=True)
    
    def append(self, trade_record: dict):
        """Append a closed trade record to the journal."""
        record = {
            "timestamp": datetime.now(ET).isoformat(),
            **trade_record,
        }
        try:
            with open(self.log_file, "a") as f:
                f.write(json.dumps(record) + "\n")
        except Exception as e:
            print(f"Failed to write trade log: {e}")
    
    def load_day(self, target_date: date) -> list:
        """Load all trades from a specific date."""
        trades = []
        try:
            with open(self.log_file, "r") as f:
                for line in f:
                    record = json.loads(line)
                    record_date = datetime.fromisoformat(
                        record["timestamp"]
                    ).date()
                    if record_date == target_date:
                        trades.append(record)
        except FileNotFoundError:
            pass
        return trades
    
    def load_all(self) -> list:
        """Load all trades from the journal."""
        trades = []
        try:
            with open(self.log_file, "r") as f:
                for line in f:
                    trades.append(json.loads(line))
        except FileNotFoundError:
            pass
        return trades
    
    def stats_for_date(self, target_date: date) -> dict:
        """Compute stats for a specific date."""
        trades = self.load_day(target_date)
        if not trades:
            return {}
        
        pnls = [t.get("pnl", 0) for t in trades]
        pnl_pcts = [t.get("pnl_pct", 0) for t in trades]
        
        wins = sum(1 for p in pnls if p > 0)
        losses = sum(1 for p in pnls if p < 0)
        
        total_pnl = sum(pnls)
        avg_pnl = total_pnl / len(pnls) if pnls else 0
        
        gross_wins = sum(p for p in pnls if p > 0)
        gross_loss = abs(sum(p for p in pnls if p < 0))
        pf = gross_wins / gross_loss if gross_loss > 0 else (float('inf') if gross_wins > 0 else 0)
        
        return {
            "date": str(target_date),
            "trades": len(trades),
            "wins": wins,
            "losses": losses,
            "win_rate": f"{(wins / len(pnls) * 100):.1f}%" if pnls else "0%",
            "profit_factor": f"{pf:.2f}",
            "total_pnl": f"${total_pnl:+.2f}",
            "avg_trade": f"${avg_pnl:+.2f}",
            "largest_win": f"${max(pnls):+.2f}" if pnls else "$0.00",
            "largest_loss": f"${min(pnls):+.2f}" if pnls else "$0.00",
        }


class PerformanceTracker:
    """Track cumulative performance metrics."""
    
    def __init__(self, log_file: str = "trades.jsonl"):
        self.log = TradeLog(log_file)
    
    def monthly_summary(self) -> dict:
        """Compute monthly performance breakdown."""
        trades = self.log.load_all()
        if not trades:
            return {}
        
        by_month = {}
        for trade in trades:
            ts = datetime.fromisoformat(trade["timestamp"])
            month_key = ts.strftime("%Y-%m")
            if month_key not in by_month:
                by_month[month_key] = []
            by_month[month_key].append(trade)
        
        summary = {}
        for month, month_trades in sorted(by_month.items()):
            pnls = [t.get("pnl", 0) for t in month_trades]
            wins = sum(1 for p in pnls if p > 0)
            
            summary[month] = {
                "trades": len(month_trades),
                "wins": wins,
                "win_rate": f"{(wins / len(pnls) * 100):.1f}%" if pnls else "0%",
                "total_pnl": f"${sum(pnls):+.2f}",
            }
        
        return summary
    
    def best_symbols(self, limit: int = 5) -> list:
        """Find best performing symbols by win rate."""
        trades = self.log.load_all()
        if not trades:
            return []
        
        by_symbol = {}
        for trade in trades:
            symbol = trade.get("symbol", "")
            if symbol not in by_symbol:
                by_symbol[symbol] = []
            by_symbol[symbol].append(trade)
        
        results = []
        for symbol, sym_trades in by_symbol.items():
            pnls = [t.get("pnl", 0) for t in sym_trades]
            wins = sum(1 for p in pnls if p > 0)
            win_rate = (wins / len(pnls) * 100) if pnls else 0
            total_pnl = sum(pnls)
            
            results.append({
                "symbol": symbol,
                "trades": len(sym_trades),
                "win_rate": f"{win_rate:.1f}%",
                "total_pnl": f"${total_pnl:+.2f}",
            })
        
        # Sort by win rate, then by total trades
        return sorted(
            results,
            key=lambda x: (float(x["win_rate"].rstrip("%")), x["trades"]),
            reverse=True,
        )[:limit]

