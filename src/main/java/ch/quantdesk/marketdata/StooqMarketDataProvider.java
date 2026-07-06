package ch.quantdesk.marketdata;

import java.math.BigDecimal;
import java.time.Duration;
import java.time.Instant;
import java.util.List;
import java.util.Locale;
import java.util.Map;
import java.util.concurrent.ConcurrentHashMap;
import org.springframework.boot.autoconfigure.condition.ConditionalOnProperty;
import org.springframework.stereotype.Component;
import org.springframework.web.client.RestClient;
import org.springframework.web.client.RestClientException;

@Component
@ConditionalOnProperty(name = "quantdesk.marketdata.provider", havingValue = "stooq")
public class StooqMarketDataProvider implements MarketDataProvider {

    private static final Duration CACHE_TTL = Duration.ofMinutes(15);

    private final Map<String, CachedSeries> cache = new ConcurrentHashMap<>();
    private volatile RestClient restClient;

    @Override
    public BarSeries history(String symbol) {
        CachedSeries cached = cache.get(symbol);
        if (cached != null && cached.fetchedAt().plus(CACHE_TTL).isAfter(Instant.now())) {
            return cached.series();
        }
        BarSeries series = fetch(symbol);
        cache.put(symbol, new CachedSeries(series, Instant.now()));
        return series;
    }

    @Override
    public BigDecimal lastPrice(String symbol) {
        List<Bar> bars = history(symbol).bars();
        if (bars.isEmpty()) {
            throw new IllegalStateException("No bars available from Stooq for symbol: " + symbol);
        }
        return bars.get(bars.size() - 1).close();
    }

    private BarSeries fetch(String symbol) {
        String csv;
        try {
            csv = client().get()
                    .uri("https://stooq.com/q/d/l/?s={symbol}&i=d", symbol.toLowerCase(Locale.ROOT))
                    .retrieve()
                    .body(String.class);
        } catch (RestClientException e) {
            throw new IllegalStateException("Failed to fetch market data from Stooq for symbol: " + symbol, e);
        }
        if (csv == null || csv.isBlank()) {
            throw new IllegalStateException("Empty response from Stooq for symbol: " + symbol);
        }
        return StooqCsvParser.parse(symbol, csv);
    }

    private RestClient client() {
        RestClient client = restClient;
        if (client == null) {
            synchronized (this) {
                if (restClient == null) {
                    restClient = RestClient.create();
                }
                client = restClient;
            }
        }
        return client;
    }

    private record CachedSeries(BarSeries series, Instant fetchedAt) {
    }
}
