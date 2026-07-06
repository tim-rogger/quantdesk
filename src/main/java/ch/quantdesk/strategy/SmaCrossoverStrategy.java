package ch.quantdesk.strategy;

import ch.quantdesk.indicator.Indicators;
import ch.quantdesk.marketdata.BarSeries;
import java.math.BigDecimal;
import java.util.List;

public class SmaCrossoverStrategy implements Strategy {

    private final int fast;
    private final int slow;

    public SmaCrossoverStrategy(int fast, int slow) {
        if (fast <= 0 || slow <= 0) {
            throw new IllegalArgumentException("SMA periods must be positive");
        }
        if (fast >= slow) {
            throw new IllegalArgumentException("Fast period must be smaller than slow period");
        }
        this.fast = fast;
        this.slow = slow;
    }

    @Override
    public String name() {
        return "SmaCrossover(" + fast + "," + slow + ")";
    }

    @Override
    public Signal decide(BarSeries series, int index) {
        if (index < 1) {
            return Signal.HOLD;
        }
        List<BigDecimal> closes = series.closes();
        List<BigDecimal> fastSma = Indicators.sma(closes, fast);
        List<BigDecimal> slowSma = Indicators.sma(closes, slow);
        BigDecimal fastPrev = fastSma.get(index - 1);
        BigDecimal slowPrev = slowSma.get(index - 1);
        BigDecimal fastNow = fastSma.get(index);
        BigDecimal slowNow = slowSma.get(index);
        if (fastPrev == null || slowPrev == null || fastNow == null || slowNow == null) {
            return Signal.HOLD;
        }
        boolean wasBelowOrEqual = fastPrev.compareTo(slowPrev) <= 0;
        boolean isAbove = fastNow.compareTo(slowNow) > 0;
        boolean wasAboveOrEqual = fastPrev.compareTo(slowPrev) >= 0;
        boolean isBelow = fastNow.compareTo(slowNow) < 0;
        if (wasBelowOrEqual && isAbove) {
            return Signal.BUY;
        }
        if (wasAboveOrEqual && isBelow) {
            return Signal.SELL;
        }
        return Signal.HOLD;
    }
}
