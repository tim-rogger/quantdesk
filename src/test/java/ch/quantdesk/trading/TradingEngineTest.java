package ch.quantdesk.trading;

import static org.assertj.core.api.Assertions.assertThat;
import static org.mockito.Mockito.mock;

import ch.quantdesk.backtest.Side;
import ch.quantdesk.execution.BrokerGateway;
import ch.quantdesk.execution.OrderRequest;
import ch.quantdesk.execution.OrderResult;
import ch.quantdesk.execution.Position;
import ch.quantdesk.marketdata.Bar;
import ch.quantdesk.marketdata.BarSeries;
import ch.quantdesk.marketdata.MarketDataProvider;
import ch.quantdesk.universe.UniverseScanner;
import java.math.BigDecimal;
import java.time.LocalDate;
import java.util.ArrayList;
import java.util.List;
import java.util.Optional;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;

class TradingEngineTest {

    private final FakeMarketData marketData = new FakeMarketData();
    private final FakeBroker broker = new FakeBroker();
    private final TradingProperties tradingProperties = new TradingProperties();
    private final RiskProperties riskProperties = new RiskProperties();
    private final TradeLog tradeLog = new TradeLog();
    private final TradingEngine engine = new TradingEngine(
            marketData,
            broker,
            tradingProperties,
            riskProperties,
            new RiskManager(riskProperties),
            tradeLog,
            mock(UniverseScanner.class),
            Optional.empty(),
            Optional.empty(),
            false);

    @BeforeEach
    void setUp() {
        tradingProperties.setSymbols(List.of("TEST"));
        tradingProperties.getStrategy().setFast(2);
        tradingProperties.getStrategy().setSlow(3);
    }

    @Test
    void dryRunLogsBuyDecisionAndPlacesNoOrder() {
        tradingProperties.setMode(TradingMode.DRY_RUN);

        engine.cycle();

        assertThat(broker.orders).isEmpty();
        assertThat(tradeLog.entries())
                .extracting(TradeLogEntry::action)
                .contains("DRY_BUY");
        TradeLogEntry entry = tradeLog.entries().stream()
                .filter(e -> e.action().equals("DRY_BUY"))
                .findFirst()
                .orElseThrow();
        assertThat(entry.symbol()).isEqualTo("TEST");
        assertThat(entry.quantity()).isEqualTo(133L);
        assertThat(entry.mode()).isEqualTo("DRY_RUN");
    }

    @Test
    void paperModePlacesOrderThroughBroker() {
        tradingProperties.setMode(TradingMode.PAPER);

        engine.cycle();

        assertThat(broker.orders).hasSize(1);
        OrderRequest order = broker.orders.get(0);
        assertThat(order.symbol()).isEqualTo("TEST");
        assertThat(order.side()).isEqualTo(Side.BUY);
        assertThat(order.quantity()).isEqualTo(133L);
        assertThat(tradeLog.entries())
                .extracting(TradeLogEntry::action)
                .contains("BUY");
    }

    @Test
    void killSwitchStopsCycles() {
        tradingProperties.setMode(TradingMode.PAPER);
        engine.cycle();
        int orderCount = broker.orders.size();
        int logSize = tradeLog.entries().size();

        tradingProperties.setMode(TradingMode.OFF);
        engine.cycle();

        assertThat(broker.orders).hasSize(orderCount);
        assertThat(tradeLog.entries()).hasSize(logSize);
    }

    @Test
    void offModeDoesNothing() {
        tradingProperties.setMode(TradingMode.OFF);

        engine.cycle();

        assertThat(broker.orders).isEmpty();
        assertThat(tradeLog.entries()).isEmpty();
        assertThat(engine.lastCycleAt()).isNull();
    }

    @Test
    void badSymbolDoesNotKillTheCycle() {
        tradingProperties.setMode(TradingMode.DRY_RUN);
        tradingProperties.setSymbols(List.of("BOOM", "TEST"));

        engine.cycle();

        assertThat(tradeLog.entries())
                .extracting(TradeLogEntry::action)
                .contains("ERROR", "DRY_BUY");
    }

    private static final class FakeMarketData implements MarketDataProvider {

        @Override
        public BarSeries history(String symbol) {
            if (symbol.equals("BOOM")) {
                throw new IllegalStateException("no data for " + symbol);
            }
            List<BigDecimal> closes = List.of(
                    new BigDecimal("10"),
                    new BigDecimal("10"),
                    new BigDecimal("10"),
                    new BigDecimal("10"),
                    new BigDecimal("15"));
            List<Bar> bars = new ArrayList<>(closes.size());
            for (int i = 0; i < closes.size(); i++) {
                BigDecimal close = closes.get(i);
                bars.add(new Bar(LocalDate.of(2024, 1, i + 1), close, close, close, close, 1000L));
            }
            return new BarSeries(symbol, bars);
        }

        @Override
        public BigDecimal lastPrice(String symbol) {
            if (symbol.equals("BOOM")) {
                throw new IllegalStateException("no price for " + symbol);
            }
            return new BigDecimal("15");
        }
    }

    private static final class FakeBroker implements BrokerGateway {

        private final List<OrderRequest> orders = new ArrayList<>();

        @Override
        public OrderResult placeOrder(OrderRequest request) {
            orders.add(request);
            return new OrderResult("FAKE-" + orders.size(), "FILLED", new BigDecimal("15"));
        }

        @Override
        public List<Position> positions() {
            return List.of();
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
