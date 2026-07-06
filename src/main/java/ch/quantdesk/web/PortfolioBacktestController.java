package ch.quantdesk.web;

import ch.quantdesk.backtest.PortfolioBacktestEngine;
import ch.quantdesk.backtest.PortfolioBacktestResult;
import ch.quantdesk.marketdata.BarSeries;
import ch.quantdesk.marketdata.MarketDataProvider;
import ch.quantdesk.universe.UniverseProperties;
import ch.quantdesk.universe.UniverseProvider;
import java.math.BigDecimal;
import java.util.LinkedHashMap;
import java.util.Map;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.RequestParam;
import org.springframework.web.bind.annotation.RestController;

@RestController
public class PortfolioBacktestController {

    private static final Logger log = LoggerFactory.getLogger(PortfolioBacktestController.class);

    private final UniverseProvider universeProvider;
    private final UniverseProperties universeProperties;
    private final MarketDataProvider marketDataProvider;
    private final PortfolioBacktestEngine engine;
    private final BigDecimal startingCash;

    public PortfolioBacktestController(
            UniverseProvider universeProvider,
            UniverseProperties universeProperties,
            MarketDataProvider marketDataProvider,
            PortfolioBacktestEngine engine,
            @Value("${quantdesk.backtest.starting-cash}") BigDecimal startingCash) {
        this.universeProvider = universeProvider;
        this.universeProperties = universeProperties;
        this.marketDataProvider = marketDataProvider;
        this.engine = engine;
        this.startingCash = startingCash;
    }

    @GetMapping("/backtest/portfolio")
    public PortfolioBacktestResponse portfolio(
            @RequestParam(required = false) Integer topN,
            @RequestParam(defaultValue = "63") int lookbackDays,
            @RequestParam(defaultValue = "21") int rebalanceDays) {
        int effectiveTopN = topN != null ? topN : universeProperties.getTopN();
        Map<String, BarSeries> seriesBySymbol = new LinkedHashMap<>();
        for (String symbol : universeProvider.symbols()) {
            try {
                seriesBySymbol.put(symbol, marketDataProvider.history(symbol));
            } catch (RuntimeException e) {
                log.warn("Skipping symbol {} in portfolio backtest: {}", symbol, e.getMessage());
            }
        }
        PortfolioBacktestResult result =
                engine.run(seriesBySymbol, effectiveTopN, lookbackDays, rebalanceDays, startingCash);
        return new PortfolioBacktestResponse(
                result.universeSize(),
                result.topN(),
                result.lookbackDays(),
                result.rebalanceEveryDays(),
                result.totalReturnPct(),
                result.maxDrawdownPct(),
                result.sharpe(),
                result.rebalanceCount());
    }
}
