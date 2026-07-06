package ch.quantdesk.backtest;

import java.math.BigDecimal;
import java.util.List;

public record PortfolioBacktestResult(
        int universeSize,
        int topN,
        int lookbackDays,
        int rebalanceEveryDays,
        List<BigDecimal> equityCurve,
        double totalReturnPct,
        double maxDrawdownPct,
        double sharpe,
        int rebalanceCount) {
}
