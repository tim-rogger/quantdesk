package ch.quantdesk.strategy.impl;

import ch.quantdesk.indicator.Indicators;
import ch.quantdesk.marketdata.BarSeries;
import ch.quantdesk.strategy.Signal;
import ch.quantdesk.strategy.Strategy;
import java.math.BigDecimal;
import java.util.List;

public class MomentumStrategy implements Strategy {

    private final int period;

    public MomentumStrategy(int period) {
        if (period <= 0) {
            throw new IllegalArgumentException("Momentum period must be positive");
        }
        this.period = period;
    }

    @Override
    public String name() {
        return "Momentum(" + period + ")";
    }

    @Override
    public Signal decide(BarSeries series, int index) {
        List<BigDecimal> closes = series.closes();
        List<BigDecimal> sma = Indicators.sma(closes, period);
        BigDecimal smaNow = sma.get(index);
        if (smaNow == null) {
            return Signal.HOLD;
        }
        BigDecimal close = closes.get(index);
        int comparison = close.compareTo(smaNow);
        if (comparison > 0) {
            return Signal.BUY;
        }
        if (comparison < 0) {
            return Signal.SELL;
        }
        return Signal.HOLD;
    }
}
