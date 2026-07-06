package ch.quantdesk.backtest;

import java.math.BigDecimal;
import java.time.LocalDate;

public record Trade(LocalDate date, Side side, BigDecimal price, long quantity) {
}
