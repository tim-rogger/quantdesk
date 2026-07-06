package ch.quantdesk.strategy.impl;

import static org.assertj.core.api.Assertions.assertThat;

import ch.quantdesk.marketdata.BarSeries;
import ch.quantdesk.marketdata.CsvBarLoader;
import ch.quantdesk.strategy.Signal;
import org.junit.jupiter.api.Test;

class RsiStrategyTest {

    private final CsvBarLoader loader = new CsvBarLoader();

    @Test
    void decidesNonNullSignalsAcrossSampleSeries() {
        BarSeries series = loader.load("SAMPLE");
        assertThat(series.size()).isGreaterThan(0);

        RsiStrategy strategy = new RsiStrategy(14, 30.0, 70.0);
        assertThat(strategy.name()).isNotBlank();

        for (int index = 0; index < series.size(); index++) {
            Signal signal = strategy.decide(series, index);
            assertThat(signal).isNotNull();
        }
    }
}
