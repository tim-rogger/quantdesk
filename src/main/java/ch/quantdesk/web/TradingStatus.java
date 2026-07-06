package ch.quantdesk.web;

import java.math.BigDecimal;
import java.time.Instant;
import java.util.List;

public record TradingStatus(
        String mode,
        List<String> symbols,
        Instant lastCycleAt,
        BigDecimal cash,
        BigDecimal equity,
        boolean brokerConnected) {
}
