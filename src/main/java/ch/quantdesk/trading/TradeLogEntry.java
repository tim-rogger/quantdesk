package ch.quantdesk.trading;

import java.math.BigDecimal;
import java.time.Instant;

public record TradeLogEntry(
        Instant timestamp,
        String symbol,
        String action,
        String reason,
        long quantity,
        BigDecimal price,
        String mode) {
}
