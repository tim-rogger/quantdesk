package ch.quantdesk.strategy.impl;

import ch.quantdesk.marketdata.BarSeries;
import ch.quantdesk.strategy.Signal;
import ch.quantdesk.strategy.Strategy;

public class BuyAndHoldStrategy implements Strategy {

    @Override
    public String name() {
        return "BuyAndHold";
    }

    @Override
    public Signal decide(BarSeries series, int index) {
        if (index == 0) {
            return Signal.BUY;
        }
        return Signal.HOLD;
    }
}
