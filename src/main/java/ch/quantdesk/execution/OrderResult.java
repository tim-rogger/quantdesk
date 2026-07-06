package ch.quantdesk.execution;

import java.math.BigDecimal;

public record OrderResult(String orderId, String status, BigDecimal fillPrice) {
}
