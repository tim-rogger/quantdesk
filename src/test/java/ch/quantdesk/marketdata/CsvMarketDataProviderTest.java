package ch.quantdesk.marketdata;

import static org.assertj.core.api.Assertions.assertThat;

import java.math.BigDecimal;
import org.junit.jupiter.api.Test;

class CsvMarketDataProviderTest {

    private final CsvMarketDataProvider provider = new CsvMarketDataProvider(new CsvBarLoader());

    @Test
    void historyLoadsSampleSeries() {
        BarSeries series = provider.history("SAMPLE");

        assertThat(series.symbol()).isEqualTo("SAMPLE");
        assertThat(series.size()).isGreaterThan(0);
    }

    @Test
    void lastPriceIsLastCloseOfHistory() {
        BarSeries series = provider.history("SAMPLE");
        BigDecimal lastClose = series.get(series.size() - 1).close();

        assertThat(provider.lastPrice("SAMPLE")).isEqualByComparingTo(lastClose);
        assertThat(provider.lastPrice("SAMPLE")).isEqualByComparingTo(new BigDecimal("127.12"));
    }
}
