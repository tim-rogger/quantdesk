package ch.quantdesk.trading;

import ch.quantdesk.backtest.Side;
import ch.quantdesk.execution.OrderRequest;
import java.math.BigDecimal;
import org.springframework.stereotype.Component;

@Component
public class RiskManager {

    private final RiskProperties properties;
    private volatile String lastRejectReason = "";

    public RiskManager(RiskProperties properties) {
        this.properties = properties;
    }

    public boolean allowOrder(OrderRequest request, BigDecimal price, BigDecimal cash,
                              BigDecimal equity, BigDecimal dayStartEquity) {
        if (request.side() != Side.BUY) {
            lastRejectReason = "";
            return true;
        }
        BigDecimal lossFloor = dayStartEquity
                .multiply(BigDecimal.valueOf(1.0 - properties.getMaxDailyLossPct() / 100.0));
        if (equity.compareTo(lossFloor) < 0) {
            lastRejectReason = "daily loss limit reached";
            return false;
        }
        BigDecimal orderValue = price.multiply(BigDecimal.valueOf(request.quantity()));
        if (orderValue.compareTo(cash) > 0) {
            lastRejectReason = "insufficient cash";
            return false;
        }
        BigDecimal maxValue = equity.multiply(BigDecimal.valueOf(properties.getMaxPositionFraction()));
        if (orderValue.compareTo(maxValue) > 0) {
            lastRejectReason = "position value exceeds max fraction of equity";
            return false;
        }
        lastRejectReason = "";
        return true;
    }

    public String lastRejectReason() {
        return lastRejectReason;
    }
}
