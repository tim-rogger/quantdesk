package ch.quantdesk.risk;

import java.math.BigDecimal;
import java.math.RoundingMode;

public final class PositionSizer {

    private PositionSizer() {
    }

    public static long fixedFraction(BigDecimal cash, BigDecimal price, double fraction) {
        if (cash == null || price == null) {
            throw new IllegalArgumentException("cash and price must not be null");
        }
        if (cash.signum() < 0) {
            throw new IllegalArgumentException("cash must not be negative");
        }
        if (price.signum() <= 0) {
            throw new IllegalArgumentException("price must be positive");
        }
        if (fraction < 0.0 || fraction > 1.0) {
            throw new IllegalArgumentException("fraction must be between 0 and 1");
        }
        BigDecimal allocation = cash.multiply(BigDecimal.valueOf(fraction));
        return allocation.divideToIntegralValue(price).setScale(0, RoundingMode.FLOOR).longValueExact();
    }
}
