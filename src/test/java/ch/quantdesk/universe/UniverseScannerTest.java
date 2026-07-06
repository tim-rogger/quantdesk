package ch.quantdesk.universe;

import static org.assertj.core.api.Assertions.assertThat;

import ch.quantdesk.marketdata.Bar;
import ch.quantdesk.marketdata.BarSeries;
import ch.quantdesk.marketdata.MarketDataProvider;
import ch.quantdesk.trading.TradingMode;
import ch.quantdesk.trading.TradingProperties;
import java.math.BigDecimal;
import java.time.LocalDate;
import java.util.ArrayList;
import java.util.List;
import java.util.Map;
import org.junit.jupiter.api.Test;

class UniverseScannerTest {

    private final UniverseProperties properties = new UniverseProperties();
    private final TradingProperties tradingProperties = new TradingProperties();

    private UniverseScanner scannerFor(List<String> symbols, Map<String, BarSeries> data, boolean universeMode) {
        return new UniverseScanner(
                new FakeUniverse(symbols),
                new FakeMarketData(data),
                properties,
                tradingProperties,
                universeMode);
    }

    private UniverseScanner defaultScanner() {
        properties.setLookbackDays(4);
        properties.setTopN(2);
        return scannerFor(
                List.of("RISER", "FLAT", "BOOM"),
                Map.of(
                        "RISER", series("RISER", "10", "11", "12", "15", "20"),
                        "FLAT", series("FLAT", "10", "10", "10", "10", "10")),
                false);
    }

    @Test
    void scanRanksRiserFirstAndFailingSymbolLast() {
        UniverseScanner scanner = defaultScanner();

        List<ScanResult> results = scanner.scan();

        assertThat(results).hasSize(3);
        assertThat(results.get(0).symbol()).isEqualTo("RISER");
        assertThat(results.get(0).momentumScore()).isEqualTo(1.0);
        assertThat(results.get(0).lastPrice()).isEqualByComparingTo(new BigDecimal("20"));
        assertThat(results.get(0).dataAvailable()).isTrue();
        assertThat(results.get(1).symbol()).isEqualTo("FLAT");
        assertThat(results.get(1).momentumScore()).isEqualTo(0.0);
        assertThat(results.get(1).dataAvailable()).isTrue();
        assertThat(results.get(2).symbol()).isEqualTo("BOOM");
        assertThat(results.get(2).momentumScore()).isEqualTo(Double.NEGATIVE_INFINITY);
        assertThat(results.get(2).lastPrice()).isEqualByComparingTo(BigDecimal.ZERO);
        assertThat(results.get(2).dataAvailable()).isFalse();
    }

    @Test
    void scanCachesResultsAndTimestamp() {
        UniverseScanner scanner = defaultScanner();
        assertThat(scanner.getLastScan()).isEmpty();
        assertThat(scanner.lastScanAt()).isNull();

        List<ScanResult> results = scanner.scan();

        assertThat(scanner.getLastScan()).isEqualTo(results);
        assertThat(scanner.lastScanAt()).isNotNull();
    }

    @Test
    void topSymbolsExcludesNonPositiveAndUnavailable() {
        UniverseScanner scanner = defaultScanner();
        scanner.scan();

        assertThat(scanner.topSymbols()).containsExactly("RISER");
    }

    @Test
    void topSymbolsRespectsTopN() {
        properties.setLookbackDays(4);
        properties.setTopN(2);
        UniverseScanner scanner = scannerFor(
                List.of("STRONG", "MEDIUM", "MILD"),
                Map.of(
                        "STRONG", series("STRONG", "10", "11", "12", "15", "20"),
                        "MEDIUM", series("MEDIUM", "10", "11", "12", "13", "15"),
                        "MILD", series("MILD", "10", "10", "11", "11", "12")),
                false);
        scanner.scan();

        assertThat(scanner.topSymbols()).containsExactly("STRONG", "MEDIUM");
    }

    @Test
    void tooShortSeriesIsUnavailable() {
        properties.setLookbackDays(4);
        UniverseScanner scanner = scannerFor(
                List.of("SHORT"),
                Map.of("SHORT", series("SHORT", "10", "12")),
                false);

        List<ScanResult> results = scanner.scan();

        assertThat(results).hasSize(1);
        assertThat(results.get(0).dataAvailable()).isFalse();
        assertThat(results.get(0).momentumScore()).isEqualTo(Double.NEGATIVE_INFINITY);
    }

    @Test
    void scheduledScanSkipsWhenUniverseModeDisabledOrTradingOff() {
        properties.setLookbackDays(4);
        Map<String, BarSeries> data = Map.of("RISER", series("RISER", "10", "11", "12", "15", "20"));

        tradingProperties.setMode(TradingMode.PAPER);
        UniverseScanner disabled = scannerFor(List.of("RISER"), data, false);
        disabled.scheduledScan();
        assertThat(disabled.lastScanAt()).isNull();

        tradingProperties.setMode(TradingMode.OFF);
        UniverseScanner off = scannerFor(List.of("RISER"), data, true);
        off.scheduledScan();
        assertThat(off.lastScanAt()).isNull();
    }

    @Test
    void scheduledScanRunsWhenEnabledAndTradingActive() {
        properties.setLookbackDays(4);
        tradingProperties.setMode(TradingMode.PAPER);
        UniverseScanner scanner = scannerFor(
                List.of("RISER"),
                Map.of("RISER", series("RISER", "10", "11", "12", "15", "20")),
                true);

        scanner.scheduledScan();

        assertThat(scanner.lastScanAt()).isNotNull();
        assertThat(scanner.getLastScan()).hasSize(1);
    }

    private static BarSeries series(String symbol, String... closes) {
        List<Bar> bars = new ArrayList<>(closes.length);
        for (int i = 0; i < closes.length; i++) {
            BigDecimal close = new BigDecimal(closes[i]);
            bars.add(new Bar(LocalDate.of(2024, 1, i + 1), close, close, close, close, 1000L));
        }
        return new BarSeries(symbol, bars);
    }

    private static final class FakeUniverse implements UniverseProvider {

        private final List<String> symbols;

        private FakeUniverse(List<String> symbols) {
            this.symbols = symbols;
        }

        @Override
        public List<String> symbols() {
            return symbols;
        }
    }

    private static final class FakeMarketData implements MarketDataProvider {

        private final Map<String, BarSeries> data;

        private FakeMarketData(Map<String, BarSeries> data) {
            this.data = data;
        }

        @Override
        public BarSeries history(String symbol) {
            BarSeries series = data.get(symbol);
            if (series == null) {
                throw new IllegalStateException("no data for " + symbol);
            }
            return series;
        }

        @Override
        public BigDecimal lastPrice(String symbol) {
            BarSeries series = history(symbol);
            return series.get(series.size() - 1).close();
        }
    }
}
