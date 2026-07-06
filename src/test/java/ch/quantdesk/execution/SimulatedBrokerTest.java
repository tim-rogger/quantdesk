package ch.quantdesk.execution;

import static org.assertj.core.api.Assertions.assertThat;

import ch.quantdesk.backtest.Side;
import ch.quantdesk.marketdata.BarSeries;
import ch.quantdesk.marketdata.MarketDataProvider;
import java.math.BigDecimal;
import org.junit.jupiter.api.Test;

class SimulatedBrokerTest {

    private final FixedPriceMarketData marketData = new FixedPriceMarketData();
    private final SimulatedBroker broker = new SimulatedBroker(marketData, new BigDecimal("10000.00"));

    @Test
    void buyReducesCashAndOpensPosition() {
        OrderResult result = broker.placeOrder(new OrderRequest("AAPL.US", Side.BUY, 10L));

        assertThat(result.status()).isEqualTo("FILLED");
        assertThat(result.fillPrice()).isEqualByComparingTo("100");
        assertThat(broker.cash()).isEqualByComparingTo("9000.00");
        assertThat(broker.positions()).hasSize(1);
        Position position = broker.positions().get(0);
        assertThat(position.symbol()).isEqualTo("AAPL.US");
        assertThat(position.quantity()).isEqualTo(10L);
        assertThat(position.avgPrice()).isEqualByComparingTo("100");
    }

    @Test
    void sellClosesPositionAndRealisesPnlInCash() {
        broker.placeOrder(new OrderRequest("AAPL.US", Side.BUY, 10L));
        marketData.price(new BigDecimal("110"));

        OrderResult result = broker.placeOrder(new OrderRequest("AAPL.US", Side.SELL, 10L));

        assertThat(result.status()).isEqualTo("FILLED");
        assertThat(result.fillPrice()).isEqualByComparingTo("110");
        assertThat(broker.positions()).isEmpty();
        assertThat(broker.cash()).isEqualByComparingTo("10100.00");
    }

    @Test
    void rejectsBuyWhenCashInsufficient() {
        OrderResult result = broker.placeOrder(new OrderRequest("AAPL.US", Side.BUY, 200L));

        assertThat(result.status()).isEqualTo("REJECTED");
        assertThat(broker.cash()).isEqualByComparingTo("10000.00");
        assertThat(broker.positions()).isEmpty();
    }

    @Test
    void rejectsSellBeyondHeldQuantity() {
        broker.placeOrder(new OrderRequest("AAPL.US", Side.BUY, 10L));

        OrderResult result = broker.placeOrder(new OrderRequest("AAPL.US", Side.SELL, 20L));

        assertThat(result.status()).isEqualTo("REJECTED");
        assertThat(broker.cash()).isEqualByComparingTo("9000.00");
        assertThat(broker.positions()).hasSize(1);
        assertThat(broker.positions().get(0).quantity()).isEqualTo(10L);
    }

    @Test
    void averagesPriceAcrossTwoBuys() {
        broker.placeOrder(new OrderRequest("AAPL.US", Side.BUY, 10L));
        marketData.price(new BigDecimal("110"));
        broker.placeOrder(new OrderRequest("AAPL.US", Side.BUY, 10L));

        assertThat(broker.positions()).hasSize(1);
        Position position = broker.positions().get(0);
        assertThat(position.quantity()).isEqualTo(20L);
        assertThat(position.avgPrice()).isEqualByComparingTo("105");
        assertThat(broker.cash()).isEqualByComparingTo("7900.00");
    }

    private static final class FixedPriceMarketData implements MarketDataProvider {

        private BigDecimal price = new BigDecimal("100");

        @Override
        public BarSeries history(String symbol) {
            throw new UnsupportedOperationException();
        }

        @Override
        public BigDecimal lastPrice(String symbol) {
            return price;
        }

        void price(BigDecimal price) {
            this.price = price;
        }
    }
}
