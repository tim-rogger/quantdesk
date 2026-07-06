package ch.quantdesk.trading;

import static org.assertj.core.api.Assertions.assertThat;
import static org.mockito.Mockito.mock;
import static org.mockito.Mockito.when;

import ch.quantdesk.backtest.Side;
import ch.quantdesk.execution.BrokerGateway;
import ch.quantdesk.execution.OrderRequest;
import ch.quantdesk.execution.OrderResult;
import ch.quantdesk.execution.Position;
import ch.quantdesk.marketdata.BarSeries;
import ch.quantdesk.marketdata.MarketDataProvider;
import ch.quantdesk.news.NewsProvider;
import ch.quantdesk.news.SentimentAnalyzer;
import ch.quantdesk.news.SentimentScore;
import ch.quantdesk.universe.ScanResult;
import ch.quantdesk.universe.UniverseScanner;
import java.math.BigDecimal;
import java.util.ArrayList;
import java.util.List;
import java.util.Optional;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;

class TradingEngineRebalanceTest {

    private final FakeMarketData marketData = new FakeMarketData();
    private final FakeBroker broker = new FakeBroker();
    private final TradingProperties tradingProperties = new TradingProperties();
    private final RiskProperties riskProperties = new RiskProperties();
    private final TradeLog tradeLog = new TradeLog();
    private final UniverseScanner scanner = mock(UniverseScanner.class);
    private final TradingEngine engine = new TradingEngine(
            marketData,
            broker,
            tradingProperties,
            riskProperties,
            new RiskManager(riskProperties),
            tradeLog,
            scanner,
            Optional.empty(),
            Optional.empty(),
            true);

    @BeforeEach
    void setUp() {
        when(scanner.getLastScan()).thenReturn(List.of(
                new ScanResult("AAA", 1.0, new BigDecimal("100"), true),
                new ScanResult("BBB", 0.8, new BigDecimal("100"), true)));
        when(scanner.topSymbols()).thenReturn(List.of("AAA", "BBB"));
    }

    @Test
    void paperRebalanceSellsDroppedPositionAndBuysTargetsEqualWeight() {
        tradingProperties.setMode(TradingMode.PAPER);

        engine.cycle();

        assertThat(broker.orders).hasSize(3);
        OrderRequest sell = broker.orders.get(0);
        assertThat(sell.symbol()).isEqualTo("CCC");
        assertThat(sell.side()).isEqualTo(Side.SELL);
        assertThat(sell.quantity()).isEqualTo(5L);
        assertThat(broker.orders.subList(1, 3))
                .extracting(OrderRequest::symbol)
                .containsExactly("AAA", "BBB");
        assertThat(broker.orders.subList(1, 3))
                .allSatisfy(order -> {
                    assertThat(order.side()).isEqualTo(Side.BUY);
                    assertThat(order.quantity()).isEqualTo(20L);
                });
        assertThat(tradeLog.entries())
                .extracting(TradeLogEntry::action)
                .contains("SELL", "BUY");
    }

    @Test
    void dryRunRebalanceLogsDecisionsAndPlacesNothing() {
        tradingProperties.setMode(TradingMode.DRY_RUN);

        engine.cycle();

        assertThat(broker.orders).isEmpty();
        assertThat(tradeLog.entries())
                .extracting(TradeLogEntry::action)
                .contains("DRY_SELL", "DRY_BUY");
        TradeLogEntry sellEntry = tradeLog.entries().stream()
                .filter(e -> e.action().equals("DRY_SELL"))
                .findFirst()
                .orElseThrow();
        assertThat(sellEntry.symbol()).isEqualTo("CCC");
        assertThat(sellEntry.quantity()).isEqualTo(5L);
        assertThat(tradeLog.entries())
                .filteredOn(e -> e.action().equals("DRY_BUY"))
                .extracting(TradeLogEntry::symbol)
                .containsExactly("AAA", "BBB");
    }

    @Test
    void scannerFailureLogsErrorAndCycleSurvives() {
        when(scanner.getLastScan()).thenThrow(new IllegalStateException("scan boom"));
        tradingProperties.setMode(TradingMode.PAPER);

        engine.cycle();

        assertThat(broker.orders).isEmpty();
        assertThat(tradeLog.entries())
                .extracting(TradeLogEntry::action)
                .contains("ERROR");
        assertThat(engine.lastCycleAt()).isNotNull();
    }

    @Test
    void retainedPositionIsKeptNotSold() {
        broker.positions.clear();
        broker.positions.add(new Position("AAA", 10L, new BigDecimal("95")));
        tradingProperties.setMode(TradingMode.PAPER);

        engine.cycle();

        assertThat(broker.orders)
                .extracting(OrderRequest::symbol)
                .containsExactly("BBB");
        assertThat(tradeLog.entries())
                .extracting(TradeLogEntry::action)
                .contains("KEEP", "BUY");
    }

    @Test
    void negativeNewsVetoesBuyButNotSell() {
        NewsProvider news = symbol -> List.of();
        SentimentAnalyzer analyzer = items -> new SentimentScore(-0.9, "bad");
        TradingEngine vetoEngine = new TradingEngine(
                marketData,
                broker,
                tradingProperties,
                riskProperties,
                new RiskManager(riskProperties),
                tradeLog,
                scanner,
                Optional.of(news),
                Optional.of(analyzer),
                true);
        tradingProperties.setMode(TradingMode.PAPER);

        vetoEngine.cycle();

        assertThat(broker.orders).hasSize(1);
        assertThat(broker.orders.get(0).side()).isEqualTo(Side.SELL);
        assertThat(tradeLog.entries())
                .filteredOn(e -> e.action().equals("VETO"))
                .extracting(TradeLogEntry::symbol)
                .containsExactly("AAA", "BBB");
    }

    private static final class FakeMarketData implements MarketDataProvider {

        @Override
        public BarSeries history(String symbol) {
            throw new IllegalStateException("history not used in rebalance mode");
        }

        @Override
        public BigDecimal lastPrice(String symbol) {
            return new BigDecimal("100");
        }
    }

    private static final class FakeBroker implements BrokerGateway {

        private final List<OrderRequest> orders = new ArrayList<>();
        private final List<Position> positions = new ArrayList<>(
                List.of(new Position("CCC", 5L, new BigDecimal("90"))));

        @Override
        public OrderResult placeOrder(OrderRequest request) {
            orders.add(request);
            return new OrderResult("FAKE-" + orders.size(), "FILLED", new BigDecimal("100"));
        }

        @Override
        public List<Position> positions() {
            return List.copyOf(positions);
        }

        @Override
        public BigDecimal cash() {
            return new BigDecimal("10000.00");
        }

        @Override
        public boolean isConnected() {
            return true;
        }
    }
}
