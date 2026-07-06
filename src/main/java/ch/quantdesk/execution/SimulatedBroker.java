package ch.quantdesk.execution;

import ch.quantdesk.backtest.Side;
import ch.quantdesk.marketdata.MarketDataProvider;
import java.math.BigDecimal;
import java.math.RoundingMode;
import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.boot.autoconfigure.condition.ConditionalOnProperty;
import org.springframework.stereotype.Component;

@Component
@ConditionalOnProperty(name = "quantdesk.ibkr.enabled", havingValue = "false", matchIfMissing = true)
public class SimulatedBroker implements BrokerGateway {

    private final MarketDataProvider marketDataProvider;
    private final Map<String, Position> positions = new LinkedHashMap<>();
    private BigDecimal cash;
    private long orderSequence;

    public SimulatedBroker(MarketDataProvider marketDataProvider,
                           @Value("${quantdesk.backtest.starting-cash}") BigDecimal startingCash) {
        this.marketDataProvider = marketDataProvider;
        this.cash = startingCash;
    }

    @Override
    public synchronized OrderResult placeOrder(OrderRequest request) {
        BigDecimal price = marketDataProvider.lastPrice(request.symbol());
        if (request.side() == Side.BUY) {
            return buy(request, price);
        }
        return sell(request, price);
    }

    @Override
    public synchronized List<Position> positions() {
        return new ArrayList<>(positions.values());
    }

    @Override
    public synchronized BigDecimal cash() {
        return cash;
    }

    @Override
    public boolean isConnected() {
        return true;
    }

    private OrderResult buy(OrderRequest request, BigDecimal price) {
        BigDecimal cost = price.multiply(BigDecimal.valueOf(request.quantity()));
        if (cost.compareTo(cash) > 0) {
            return new OrderResult(nextOrderId(), "REJECTED", BigDecimal.ZERO);
        }
        cash = cash.subtract(cost);
        Position existing = positions.get(request.symbol());
        if (existing == null) {
            positions.put(request.symbol(), new Position(request.symbol(), request.quantity(), price));
        } else {
            long totalQuantity = existing.quantity() + request.quantity();
            BigDecimal totalCost = existing.avgPrice()
                    .multiply(BigDecimal.valueOf(existing.quantity()))
                    .add(cost);
            BigDecimal avgPrice = totalCost.divide(BigDecimal.valueOf(totalQuantity), 10, RoundingMode.HALF_UP);
            positions.put(request.symbol(), new Position(request.symbol(), totalQuantity, avgPrice));
        }
        return new OrderResult(nextOrderId(), "FILLED", price);
    }

    private OrderResult sell(OrderRequest request, BigDecimal price) {
        Position existing = positions.get(request.symbol());
        if (existing == null || existing.quantity() < request.quantity()) {
            return new OrderResult(nextOrderId(), "REJECTED", BigDecimal.ZERO);
        }
        cash = cash.add(price.multiply(BigDecimal.valueOf(request.quantity())));
        long remaining = existing.quantity() - request.quantity();
        if (remaining == 0L) {
            positions.remove(request.symbol());
        } else {
            positions.put(request.symbol(), new Position(request.symbol(), remaining, existing.avgPrice()));
        }
        return new OrderResult(nextOrderId(), "FILLED", price);
    }

    private String nextOrderId() {
        orderSequence++;
        return "SIM-" + orderSequence;
    }
}
