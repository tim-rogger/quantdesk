package ch.quantdesk.strategy;

import ch.quantdesk.marketdata.BarSeries;

public interface Strategy {

    String name();

    Signal decide(BarSeries series, int index);
}
