package ch.quantdesk.strategy.impl;

import static org.assertj.core.api.Assertions.assertThat;

import ch.quantdesk.marketdata.BarSeries;
import ch.quantdesk.marketdata.CsvBarLoader;
import ch.quantdesk.strategy.Signal;
import org.junit.jupiter.api.Test;

class BuyAndHoldStrategyTest {

    private final CsvBarLoader loader = new CsvBarLoader();

    @Test
    void decidesNonNullSignalsAcrossSampleSeries() {
        BarSeries series = loader.load("SAMPLE");
        assertThat(series.size()).isGreaterThan(0);

        BuyAndHoldStrategy strategy = new BuyAndHoldStrategy();
        assertThat(strategy.name()).isNotBlank();

        for (int index = 0; index < series.size(); index++) {
            Signal signal = strategy.decide(series, index);
            assertThat(signal).isNotNull();
        }
        assertThat(strategy.decide(series, 0)).isEqualTo(Signal.BUY);
        assertThat(strategy.decide(series, 1)).isEqualTo(Signal.HOLD);
    }
}
