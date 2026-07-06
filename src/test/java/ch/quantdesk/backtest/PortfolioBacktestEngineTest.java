package ch.quantdesk.backtest;

import static org.assertj.core.api.Assertions.assertThat;

import ch.quantdesk.marketdata.Bar;
import ch.quantdesk.marketdata.BarSeries;
import java.math.BigDecimal;
import java.math.RoundingMode;
import java.time.LocalDate;
import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import org.junit.jupiter.api.Test;

class PortfolioBacktestEngineTest {

    private final PortfolioBacktestEngine engine = new PortfolioBacktestEngine();

    @Test
    void picksRiserAndProducesPositiveReturn() {
        Map<String, BarSeries> universe = new LinkedHashMap<>();
        universe.put("RISER", linearSeries("RISER", 80, 100.0, 200.0));
        universe.put("FALLER", linearSeries("FALLER", 80, 100.0, 50.0));
        universe.put("FLAT1", linearSeries("FLAT1", 80, 100.0, 100.0));
        universe.put("FLAT2", linearSeries("FLAT2", 80, 100.0, 100.0));

        PortfolioBacktestResult result = engine.run(universe, 2, 20, 10, new BigDecimal("10000.00"));

        assertThat(result.universeSize()).isEqualTo(4);
        assertThat(result.topN()).isEqualTo(2);
        assertThat(result.equityCurve()).hasSize(80);
        assertThat(result.rebalanceCount()).isGreaterThan(0);
        assertThat(result.totalReturnPct()).isGreaterThan(0.0);
        assertThat(result.maxDrawdownPct()).isFinite();
        assertThat(result.sharpe()).isFinite();
    }

    @Test
    void universeSmallerThanTopNStillRuns() {
        Map<String, BarSeries> universe = new LinkedHashMap<>();
        universe.put("RISER", linearSeries("RISER", 80, 100.0, 200.0));

        PortfolioBacktestResult result = engine.run(universe, 5, 20, 10, new BigDecimal("10000.00"));

        assertThat(result.universeSize()).isEqualTo(1);
        assertThat(result.equityCurve()).hasSize(80);
        assertThat(result.rebalanceCount()).isGreaterThan(0);
        assertThat(result.totalReturnPct()).isGreaterThan(0.0);
        assertThat(result.maxDrawdownPct()).isFinite();
        assertThat(result.sharpe()).isFinite();
    }

    @Test
    void skipsSeriesShorterThanLookback() {
        Map<String, BarSeries> universe = new LinkedHashMap<>();
        universe.put("RISER", linearSeries("RISER", 80, 100.0, 200.0));
        universe.put("SHORT", linearSeries("SHORT", 10, 100.0, 110.0));

        PortfolioBacktestResult result = engine.run(universe, 2, 20, 10, new BigDecimal("10000.00"));

        assertThat(result.equityCurve()).hasSize(80);
        assertThat(result.totalReturnPct()).isGreaterThan(0.0);
    }

    private BarSeries linearSeries(String symbol, int bars, double startClose, double endClose) {
        List<Bar> list = new ArrayList<>(bars);
        LocalDate start = LocalDate.of(2024, 1, 1);
        double step = (endClose - startClose) / (bars - 1);
        for (int i = 0; i < bars; i++) {
            BigDecimal close = BigDecimal.valueOf(startClose + step * i).setScale(4, RoundingMode.HALF_UP);
            list.add(new Bar(start.plusDays(i), close, close, close, close, 1000L));
        }
        return new BarSeries(symbol, list);
    }
}
