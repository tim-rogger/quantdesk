package ch.quantdesk.risk;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;

import java.math.BigDecimal;
import org.junit.jupiter.api.Test;

class RiskTest {

    @Test
    void fixedFractionFloorsShareCount() {
        long shares = PositionSizer.fixedFraction(new BigDecimal("10000.00"), new BigDecimal("330.00"), 0.5);
        assertThat(shares).isEqualTo(15L);
    }

    @Test
    void fixedFractionUsesFullCashWhenFractionIsOne() {
        long shares = PositionSizer.fixedFraction(new BigDecimal("1000.00"), new BigDecimal("100.00"), 1.0);
        assertThat(shares).isEqualTo(10L);
    }

    @Test
    void fixedFractionReturnsZeroWhenFractionIsZero() {
        long shares = PositionSizer.fixedFraction(new BigDecimal("1000.00"), new BigDecimal("100.00"), 0.0);
        assertThat(shares).isZero();
    }

    @Test
    void fixedFractionReturnsZeroWhenPriceExceedsAllocation() {
        long shares = PositionSizer.fixedFraction(new BigDecimal("100.00"), new BigDecimal("250.00"), 0.5);
        assertThat(shares).isZero();
    }

    @Test
    void fixedFractionRejectsNegativeCash() {
        assertThatThrownBy(() -> PositionSizer.fixedFraction(new BigDecimal("-1.00"), new BigDecimal("10.00"), 0.5))
                .isInstanceOf(IllegalArgumentException.class);
    }

    @Test
    void fixedFractionRejectsNonPositivePrice() {
        assertThatThrownBy(() -> PositionSizer.fixedFraction(new BigDecimal("1000.00"), BigDecimal.ZERO, 0.5))
                .isInstanceOf(IllegalArgumentException.class);
    }

    @Test
    void fixedFractionRejectsFractionOutOfRange() {
        assertThatThrownBy(() -> PositionSizer.fixedFraction(new BigDecimal("1000.00"), new BigDecimal("10.00"), 1.5))
                .isInstanceOf(IllegalArgumentException.class);
    }

    @Test
    void shouldExitTriggersAtThreshold() {
        assertThat(StopLoss.shouldExit(new BigDecimal("100.00"), new BigDecimal("90.00"), 0.10)).isTrue();
    }

    @Test
    void shouldExitTriggersBelowThreshold() {
        assertThat(StopLoss.shouldExit(new BigDecimal("100.00"), new BigDecimal("85.00"), 0.10)).isTrue();
    }

    @Test
    void shouldExitDoesNotTriggerAboveThreshold() {
        assertThat(StopLoss.shouldExit(new BigDecimal("100.00"), new BigDecimal("95.00"), 0.10)).isFalse();
    }

    @Test
    void shouldExitRejectsNonPositiveEntryPrice() {
        assertThatThrownBy(() -> StopLoss.shouldExit(BigDecimal.ZERO, new BigDecimal("90.00"), 0.10))
                .isInstanceOf(IllegalArgumentException.class);
    }

    @Test
    void shouldExitRejectsStopPctOutOfRange() {
        assertThatThrownBy(() -> StopLoss.shouldExit(new BigDecimal("100.00"), new BigDecimal("90.00"), 1.5))
                .isInstanceOf(IllegalArgumentException.class);
    }

    @Test
    void takeProfitTriggersAtThreshold() {
        assertThat(StopLoss.takeProfit(new BigDecimal("100.00"), new BigDecimal("120.00"), 0.20)).isTrue();
    }

    @Test
    void takeProfitTriggersAboveThreshold() {
        assertThat(StopLoss.takeProfit(new BigDecimal("100.00"), new BigDecimal("130.00"), 0.20)).isTrue();
    }

    @Test
    void takeProfitDoesNotTriggerBelowThreshold() {
        assertThat(StopLoss.takeProfit(new BigDecimal("100.00"), new BigDecimal("110.00"), 0.20)).isFalse();
    }

    @Test
    void takeProfitRejectsNegativeTargetPct() {
        assertThatThrownBy(() -> StopLoss.takeProfit(new BigDecimal("100.00"), new BigDecimal("110.00"), -0.10))
                .isInstanceOf(IllegalArgumentException.class);
    }
}
