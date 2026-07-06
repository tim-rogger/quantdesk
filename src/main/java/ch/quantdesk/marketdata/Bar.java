package ch.quantdesk.marketdata;

import java.math.BigDecimal;
import java.time.LocalDate;

public record Bar(LocalDate date, BigDecimal open, BigDecimal high, BigDecimal low, BigDecimal close, long volume) {
}
