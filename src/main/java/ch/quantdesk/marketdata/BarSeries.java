package ch.quantdesk.marketdata;

import java.math.BigDecimal;
import java.util.ArrayList;
import java.util.List;

public class BarSeries {

    private final String symbol;
    private final List<Bar> bars;

    public BarSeries(String symbol, List<Bar> bars) {
        this.symbol = symbol;
        this.bars = List.copyOf(bars);
    }

    public String symbol() {
        return symbol;
    }

    public List<Bar> bars() {
        return bars;
    }

    public int size() {
        return bars.size();
    }

    public Bar get(int index) {
        return bars.get(index);
    }

    public List<BigDecimal> closes() {
        List<BigDecimal> closes = new ArrayList<>(bars.size());
        for (Bar bar : bars) {
            closes.add(bar.close());
        }
        return closes;
    }
}
