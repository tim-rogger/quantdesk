package ch.quantdesk.web;

public record BacktestResponse(
        String symbol,
        String strategy,
        double totalReturnPct,
        double maxDrawdownPct,
        double sharpe,
        int tradeCount,
        double winRatePct) {
}
