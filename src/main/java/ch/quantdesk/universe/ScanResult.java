package ch.quantdesk.universe;

import java.math.BigDecimal;

public record ScanResult(String symbol, double momentumScore, BigDecimal lastPrice, boolean dataAvailable) {
}
