package ch.quantdesk.web;

import ch.quantdesk.backtest.BacktestEngine;
import ch.quantdesk.backtest.BacktestResult;
import ch.quantdesk.marketdata.BarSeries;
import ch.quantdesk.marketdata.CsvBarLoader;
import ch.quantdesk.strategy.SmaCrossoverStrategy;
import java.math.BigDecimal;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.RequestParam;
import org.springframework.web.bind.annotation.RestController;

@RestController
public class BacktestController {

    private final CsvBarLoader loader;
    private final BacktestEngine engine;
    private final BigDecimal startingCash;

    public BacktestController(
            CsvBarLoader loader,
            BacktestEngine engine,
            @Value("${quantdesk.backtest.starting-cash}") BigDecimal startingCash) {
        this.loader = loader;
        this.engine = engine;
        this.startingCash = startingCash;
    }

    @GetMapping("/backtest")
    public BacktestResponse backtest(
            @RequestParam(defaultValue = "SAMPLE") String symbol,
            @RequestParam(defaultValue = "10") int fast,
            @RequestParam(defaultValue = "30") int slow) {
        BarSeries series = loader.load(symbol);
        SmaCrossoverStrategy strategy = new SmaCrossoverStrategy(fast, slow);
        BacktestResult result = engine.run(series, strategy, startingCash);
        return new BacktestResponse(
                result.symbol(),
                result.strategy(),
                result.totalReturnPct(),
                result.maxDrawdownPct(),
                result.sharpe(),
                result.tradeCount(),
                result.winRatePct());
    }
}
