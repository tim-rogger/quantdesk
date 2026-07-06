package ch.quantdesk.backtest;

import java.math.BigDecimal;
import java.util.List;

public record BacktestResult(
        String symbol,
        String strategy,
        List<BigDecimal> equityCurve,
        List<Trade> trades,
        double totalReturnPct,
        double maxDrawdownPct,
        double sharpe,
        int tradeCount,
        double winRatePct) {
}
