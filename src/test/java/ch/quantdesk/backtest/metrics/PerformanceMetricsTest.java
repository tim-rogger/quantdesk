package ch.quantdesk.backtest.metrics;

import static org.assertj.core.api.Assertions.assertThat;

import java.math.BigDecimal;
import java.util.List;
import org.junit.jupiter.api.Test;

class PerformanceMetricsTest {

    private static final double TRADING_DAYS = 252.0;

    private final List<BigDecimal> risingCurve = List.of(
            new BigDecimal("10000.00"),
            new BigDecimal("10100.00"),
            new BigDecimal("10050.00"),
            new BigDecimal("10250.00"),
            new BigDecimal("10200.00"),
            new BigDecimal("10400.00"),
            new BigDecimal("10600.00"),
            new BigDecimal("10500.00"),
            new BigDecimal("10800.00"),
            new BigDecimal("11000.00"));

    @Test
    void cagrIsFiniteAndPositiveForRisingCurve() {
        double cagr = PerformanceMetrics.cagr(risingCurve, TRADING_DAYS);
        assertThat(cagr).isFinite();
        assertThat(cagr).isGreaterThan(0.0);
    }

    @Test
    void annualizedVolatilityIsFiniteAndPositive() {
        double volatility = PerformanceMetrics.annualizedVolatility(risingCurve);
        assertThat(volatility).isFinite();
        assertThat(volatility).isGreaterThan(0.0);
    }

    @Test
    void sortinoIsFiniteAndPositiveWhenUpsideDominates() {
        double sortino = PerformanceMetrics.sortino(risingCurve);
        assertThat(sortino).isFinite();
        assertThat(sortino).isGreaterThan(0.0);
    }

    @Test
    void calmarIsFiniteAndPositiveForRisingCurve() {
        double calmar = PerformanceMetrics.calmar(risingCurve);
        assertThat(calmar).isFinite();
        assertThat(calmar).isGreaterThan(0.0);
    }

    @Test
    void metricsAreZeroForFlatCurve() {
        List<BigDecimal> flat = List.of(
                new BigDecimal("10000.00"),
                new BigDecimal("10000.00"),
                new BigDecimal("10000.00"));
        assertThat(PerformanceMetrics.cagr(flat, TRADING_DAYS)).isZero();
        assertThat(PerformanceMetrics.annualizedVolatility(flat)).isZero();
        assertThat(PerformanceMetrics.sortino(flat)).isZero();
        assertThat(PerformanceMetrics.calmar(flat)).isZero();
    }

    @Test
    void metricsHandleSingleElementCurve() {
        List<BigDecimal> single = List.of(new BigDecimal("10000.00"));
        assertThat(PerformanceMetrics.cagr(single, TRADING_DAYS)).isFinite().isZero();
        assertThat(PerformanceMetrics.annualizedVolatility(single)).isFinite().isZero();
        assertThat(PerformanceMetrics.sortino(single)).isFinite().isZero();
        assertThat(PerformanceMetrics.calmar(single)).isFinite().isZero();
    }
}
