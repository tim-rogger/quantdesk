package ch.quantdesk.trading;

import static org.assertj.core.api.Assertions.assertThat;

import ch.quantdesk.backtest.Side;
import ch.quantdesk.execution.OrderRequest;
import java.math.BigDecimal;
import org.junit.jupiter.api.Test;

class RiskManagerTest {

    private final RiskProperties properties = new RiskProperties();
    private final RiskManager riskManager = new RiskManager(properties);

    @Test
    void allowsBuyWithinPositionCap() {
        boolean allowed = riskManager.allowOrder(
                new OrderRequest("TEST", Side.BUY, 10L),
                new BigDecimal("100"),
                new BigDecimal("10000"),
                new BigDecimal("10000"),
                new BigDecimal("10000"));

        assertThat(allowed).isTrue();
        assertThat(riskManager.lastRejectReason()).isEmpty();
    }

    @Test
    void blocksBuyExceedingPositionCap() {
        boolean allowed = riskManager.allowOrder(
                new OrderRequest("TEST", Side.BUY, 30L),
                new BigDecimal("100"),
                new BigDecimal("10000"),
                new BigDecimal("10000"),
                new BigDecimal("10000"));

        assertThat(allowed).isFalse();
        assertThat(riskManager.lastRejectReason()).contains("max fraction");
    }

    @Test
    void blocksAllBuysWhenDailyLossLimitBreached() {
        boolean allowed = riskManager.allowOrder(
                new OrderRequest("TEST", Side.BUY, 1L),
                new BigDecimal("100"),
                new BigDecimal("9700"),
                new BigDecimal("9700"),
                new BigDecimal("10000"));

        assertThat(allowed).isFalse();
        assertThat(riskManager.lastRejectReason()).isEqualTo("daily loss limit reached");
    }

    @Test
    void allowsSellEvenWhenDailyLossLimitBreached() {
        boolean allowed = riskManager.allowOrder(
                new OrderRequest("TEST", Side.SELL, 50L),
                new BigDecimal("100"),
                new BigDecimal("9000"),
                new BigDecimal("9000"),
                new BigDecimal("10000"));

        assertThat(allowed).isTrue();
    }

    @Test
    void blocksBuyExceedingAvailableCash() {
        boolean allowed = riskManager.allowOrder(
                new OrderRequest("TEST", Side.BUY, 10L),
                new BigDecimal("100"),
                new BigDecimal("500"),
                new BigDecimal("10000"),
                new BigDecimal("10000"));

        assertThat(allowed).isFalse();
        assertThat(riskManager.lastRejectReason()).isEqualTo("insufficient cash");
    }

    @Test
    void allowsBuyExactlyAtDailyLossFloor() {
        boolean allowed = riskManager.allowOrder(
                new OrderRequest("TEST", Side.BUY, 10L),
                new BigDecimal("100"),
                new BigDecimal("9800"),
                new BigDecimal("9800"),
                new BigDecimal("10000"));

        assertThat(allowed).isTrue();
    }
}
