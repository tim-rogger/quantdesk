package ch.quantdesk.web;

public record PortfolioBacktestResponse(
        int universeSize,
        int topN,
        int lookbackDays,
        int rebalanceEveryDays,
        double totalReturnPct,
        double maxDrawdownPct,
        double sharpe,
        int rebalanceCount) {
}
