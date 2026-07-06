package ch.quantdesk.marketdata;

import java.math.BigDecimal;
import java.util.List;
import org.springframework.boot.autoconfigure.condition.ConditionalOnProperty;
import org.springframework.stereotype.Component;

@Component
@ConditionalOnProperty(name = "quantdesk.marketdata.provider", havingValue = "csv")
public class CsvMarketDataProvider implements MarketDataProvider {

    private final CsvBarLoader loader;

    public CsvMarketDataProvider(CsvBarLoader loader) {
        this.loader = loader;
    }

    @Override
    public BarSeries history(String symbol) {
        return loader.load(symbol);
    }

    @Override
    public BigDecimal lastPrice(String symbol) {
        List<Bar> bars = history(symbol).bars();
        if (bars.isEmpty()) {
            throw new IllegalStateException("No bars available for symbol: " + symbol);
        }
        return bars.get(bars.size() - 1).close();
    }
}
