package ch.quantdesk.execution;

import java.math.BigDecimal;

public record Position(String symbol, long quantity, BigDecimal avgPrice) {
}
