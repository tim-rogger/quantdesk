package ch.quantdesk.marketdata;

import java.math.BigDecimal;

public interface MarketDataProvider {

    BarSeries history(String symbol);

    BigDecimal lastPrice(String symbol);
}
