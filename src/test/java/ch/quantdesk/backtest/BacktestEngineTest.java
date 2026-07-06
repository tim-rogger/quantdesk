package ch.quantdesk.backtest;

import static org.assertj.core.api.Assertions.assertThat;

import ch.quantdesk.marketdata.BarSeries;
import ch.quantdesk.marketdata.CsvBarLoader;
import ch.quantdesk.strategy.SmaCrossoverStrategy;
import java.math.BigDecimal;
import org.junit.jupiter.api.Test;

class BacktestEngineTest {

    private final CsvBarLoader loader = new CsvBarLoader();
    private final BacktestEngine engine = new BacktestEngine();

    @Test
    void runsSmaCrossoverOnSampleData() {
        BarSeries series = loader.load("SAMPLE");
        assertThat(series.size()).isGreaterThan(0);

        SmaCrossoverStrategy strategy = new SmaCrossoverStrategy(10, 30);
        BacktestResult result = engine.run(series, strategy, new BigDecimal("10000.00"));

        assertThat(result.equityCurve()).hasSize(series.size());
        assertThat(result.tradeCount()).isGreaterThanOrEqualTo(0);
        assertThat(result.totalReturnPct()).isFinite();
        assertThat(result.maxDrawdownPct()).isFinite();
        assertThat(result.sharpe()).isFinite();
        assertThat(result.winRatePct()).isFinite();
    }
}
