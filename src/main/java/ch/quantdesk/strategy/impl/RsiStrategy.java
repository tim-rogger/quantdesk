package ch.quantdesk.strategy.impl;

import ch.quantdesk.indicator.Indicators;
import ch.quantdesk.marketdata.BarSeries;
import ch.quantdesk.strategy.Signal;
import ch.quantdesk.strategy.Strategy;
import java.math.BigDecimal;
import java.util.List;

public class RsiStrategy implements Strategy {

    private final int period;
    private final double oversold;
    private final double overbought;

    public RsiStrategy() {
        this(14, 30.0, 70.0);
    }

    public RsiStrategy(int period, double oversold, double overbought) {
        if (period <= 0) {
            throw new IllegalArgumentException("RSI period must be positive");
        }
        if (oversold >= overbought) {
            throw new IllegalArgumentException("Oversold threshold must be smaller than overbought threshold");
        }
        this.period = period;
        this.oversold = oversold;
        this.overbought = overbought;
    }

    @Override
    public String name() {
        return "Rsi(" + period + "," + oversold + "," + overbought + ")";
    }

    @Override
    public Signal decide(BarSeries series, int index) {
        List<BigDecimal> closes = series.closes();
        List<Double> rsi = Indicators.rsi(closes, period);
        Double value = rsi.get(index);
        if (value == null) {
            return Signal.HOLD;
        }
        if (value < oversold) {
            return Signal.BUY;
        }
        if (value > overbought) {
            return Signal.SELL;
        }
        return Signal.HOLD;
    }
}
