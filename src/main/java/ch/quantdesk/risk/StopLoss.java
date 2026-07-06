package ch.quantdesk.risk;

import java.math.BigDecimal;

public final class StopLoss {

    private StopLoss() {
    }

    public static boolean shouldExit(BigDecimal entryPrice, BigDecimal currentPrice, double stopPct) {
        if (entryPrice == null || currentPrice == null) {
            throw new IllegalArgumentException("entryPrice and currentPrice must not be null");
        }
        if (entryPrice.signum() <= 0) {
            throw new IllegalArgumentException("entryPrice must be positive");
        }
        if (currentPrice.signum() < 0) {
            throw new IllegalArgumentException("currentPrice must not be negative");
        }
        if (stopPct < 0.0 || stopPct > 1.0) {
            throw new IllegalArgumentException("stopPct must be between 0 and 1");
        }
        BigDecimal threshold = entryPrice.multiply(BigDecimal.valueOf(1.0 - stopPct));
        return currentPrice.compareTo(threshold) <= 0;
    }

    public static boolean takeProfit(BigDecimal entryPrice, BigDecimal currentPrice, double targetPct) {
        if (entryPrice == null || currentPrice == null) {
            throw new IllegalArgumentException("entryPrice and currentPrice must not be null");
        }
        if (entryPrice.signum() <= 0) {
            throw new IllegalArgumentException("entryPrice must be positive");
        }
        if (currentPrice.signum() < 0) {
            throw new IllegalArgumentException("currentPrice must not be negative");
        }
        if (targetPct < 0.0) {
            throw new IllegalArgumentException("targetPct must not be negative");
        }
        BigDecimal threshold = entryPrice.multiply(BigDecimal.valueOf(1.0 + targetPct));
        return currentPrice.compareTo(threshold) >= 0;
    }
}
