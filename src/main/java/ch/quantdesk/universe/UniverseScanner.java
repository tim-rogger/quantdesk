package ch.quantdesk.universe;

import ch.quantdesk.marketdata.BarSeries;
import ch.quantdesk.marketdata.MarketDataProvider;
import ch.quantdesk.trading.TradingMode;
import ch.quantdesk.trading.TradingProperties;
import java.math.BigDecimal;
import java.time.Instant;
import java.util.ArrayList;
import java.util.Comparator;
import java.util.List;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.scheduling.annotation.Scheduled;
import org.springframework.stereotype.Service;

@Service
public class UniverseScanner {

    private static final Logger log = LoggerFactory.getLogger(UniverseScanner.class);
    private static final long PAUSE_MS = 120L;

    private final UniverseProvider universeProvider;
    private final MarketDataProvider marketData;
    private final UniverseProperties properties;
    private final TradingProperties tradingProperties;
    private final boolean universeMode;

    private volatile List<ScanResult> lastScan = List.of();
    private volatile Instant lastScanAt;

    public UniverseScanner(UniverseProvider universeProvider,
                           MarketDataProvider marketData,
                           UniverseProperties properties,
                           TradingProperties tradingProperties,
                           @Value("${quantdesk.trading.universe-mode:false}") boolean universeMode) {
        this.universeProvider = universeProvider;
        this.marketData = marketData;
        this.properties = properties;
        this.tradingProperties = tradingProperties;
        this.universeMode = universeMode;
    }

    public List<ScanResult> scan() {
        List<String> symbols = universeProvider.symbols();
        List<ScanResult> results = new ArrayList<>(symbols.size());
        for (int i = 0; i < symbols.size(); i++) {
            if (i > 0) {
                pause();
            }
            results.add(scanSymbol(symbols.get(i)));
        }
        results.sort(Comparator.comparingDouble(ScanResult::momentumScore).reversed());
        List<ScanResult> snapshot = List.copyOf(results);
        lastScan = snapshot;
        lastScanAt = Instant.now();
        return snapshot;
    }

    public List<String> topSymbols() {
        return lastScan.stream()
                .filter(result -> result.dataAvailable() && result.momentumScore() > 0)
                .limit(properties.getTopN())
                .map(ScanResult::symbol)
                .toList();
    }

    public List<ScanResult> getLastScan() {
        return lastScan;
    }

    public Instant lastScanAt() {
        return lastScanAt;
    }

    @Scheduled(fixedDelayString = "${quantdesk.universe.scan-interval-ms}",
            initialDelayString = "${quantdesk.universe.scan-initial-delay-ms}")
    public void scheduledScan() {
        if (!universeMode || tradingProperties.getMode() == TradingMode.OFF) {
            return;
        }
        try {
            scan();
        } catch (Exception e) {
            log.warn("Universe scan failed: {}", e.getMessage());
        }
    }

    private ScanResult scanSymbol(String symbol) {
        try {
            BarSeries series = marketData.history(symbol);
            int baseIndex = series.size() - 1 - properties.getLookbackDays();
            if (baseIndex < 0) {
                return unavailable(symbol);
            }
            BigDecimal lastClose = series.get(series.size() - 1).close();
            BigDecimal baseClose = series.get(baseIndex).close();
            double momentumScore = lastClose.doubleValue() / baseClose.doubleValue() - 1.0;
            return new ScanResult(symbol, momentumScore, lastClose, true);
        } catch (Exception e) {
            return unavailable(symbol);
        }
    }

    private ScanResult unavailable(String symbol) {
        return new ScanResult(symbol, Double.NEGATIVE_INFINITY, BigDecimal.ZERO, false);
    }

    private void pause() {
        try {
            Thread.sleep(PAUSE_MS);
        } catch (InterruptedException e) {
            Thread.currentThread().interrupt();
        }
    }
}
