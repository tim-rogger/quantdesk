package ch.quantdesk.backtest;

import ch.quantdesk.marketdata.BarSeries;
import java.math.BigDecimal;
import java.math.RoundingMode;
import java.util.ArrayList;
import java.util.Comparator;
import java.util.Iterator;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import org.springframework.stereotype.Service;

@Service
public class PortfolioBacktestEngine {

    private static final double TRADING_DAYS = 252.0;

    public PortfolioBacktestResult run(
            Map<String, BarSeries> seriesBySymbol,
            int topN,
            int lookbackDays,
            int rebalanceEveryDays,
            BigDecimal startingCash) {
        if (seriesBySymbol == null || startingCash == null) {
            throw new IllegalArgumentException("seriesBySymbol and startingCash must not be null");
        }
        if (topN <= 0 || lookbackDays <= 0 || rebalanceEveryDays <= 0) {
            throw new IllegalArgumentException("topN, lookbackDays and rebalanceEveryDays must be positive");
        }
        if (startingCash.signum() <= 0) {
            throw new IllegalArgumentException("startingCash must be positive");
        }

        Map<String, BarSeries> eligible = new LinkedHashMap<>();
        for (Map.Entry<String, BarSeries> entry : seriesBySymbol.entrySet()) {
            BarSeries series = entry.getValue();
            if (series != null && series.size() >= lookbackDays + 2) {
                eligible.put(entry.getKey(), series);
            }
        }
        if (eligible.isEmpty()) {
            return new PortfolioBacktestResult(
                    seriesBySymbol.size(), topN, lookbackDays, rebalanceEveryDays,
                    List.of(), 0.0, 0.0, 0.0, 0);
        }

        int length = Integer.MAX_VALUE;
        for (BarSeries series : eligible.values()) {
            length = Math.min(length, series.size());
        }
        Map<String, Integer> offsets = new LinkedHashMap<>();
        for (Map.Entry<String, BarSeries> entry : eligible.entrySet()) {
            offsets.put(entry.getKey(), entry.getValue().size() - length);
        }

        BigDecimal cash = startingCash;
        Map<String, Long> holdings = new LinkedHashMap<>();
        List<BigDecimal> equityCurve = new ArrayList<>(length);
        int rebalanceCount = 0;

        for (int t = 0; t < length; t++) {
            if (t >= lookbackDays && (t - lookbackDays) % rebalanceEveryDays == 0) {
                List<String> target = selectTop(eligible, offsets, t, topN, lookbackDays);
                cash = sellDropouts(eligible, offsets, holdings, target, cash, t);
                cash = buyEntrants(eligible, offsets, holdings, target, cash, t);
                rebalanceCount++;
            }
            BigDecimal equity = cash;
            for (Map.Entry<String, Long> holding : holdings.entrySet()) {
                BigDecimal close = closeAt(eligible.get(holding.getKey()), offsets.get(holding.getKey()), t);
                equity = equity.add(close.multiply(BigDecimal.valueOf(holding.getValue())));
            }
            equityCurve.add(equity);
        }

        return new PortfolioBacktestResult(
                seriesBySymbol.size(),
                topN,
                lookbackDays,
                rebalanceEveryDays,
                equityCurve,
                totalReturnPct(startingCash, equityCurve),
                maxDrawdownPct(equityCurve),
                sharpe(equityCurve),
                rebalanceCount);
    }

    private List<String> selectTop(
            Map<String, BarSeries> eligible,
            Map<String, Integer> offsets,
            int t,
            int topN,
            int lookbackDays) {
        List<Map.Entry<String, Double>> ranked = new ArrayList<>();
        for (String symbol : eligible.keySet()) {
            double current = closeAt(eligible.get(symbol), offsets.get(symbol), t).doubleValue();
            double past = closeAt(eligible.get(symbol), offsets.get(symbol), t - lookbackDays).doubleValue();
            if (past != 0.0) {
                double momentum = current / past - 1.0;
                if (momentum > 0.0) {
                    ranked.add(Map.entry(symbol, momentum));
                }
            }
        }
        ranked.sort(Comparator
                .comparingDouble((Map.Entry<String, Double> entry) -> entry.getValue())
                .reversed()
                .thenComparing(Map.Entry::getKey));
        List<String> target = new ArrayList<>();
        for (int i = 0; i < Math.min(topN, ranked.size()); i++) {
            target.add(ranked.get(i).getKey());
        }
        return target;
    }

    private BigDecimal sellDropouts(
            Map<String, BarSeries> eligible,
            Map<String, Integer> offsets,
            Map<String, Long> holdings,
            List<String> target,
            BigDecimal cash,
            int t) {
        Iterator<Map.Entry<String, Long>> iterator = holdings.entrySet().iterator();
        while (iterator.hasNext()) {
            Map.Entry<String, Long> holding = iterator.next();
            if (!target.contains(holding.getKey())) {
                BigDecimal close = closeAt(eligible.get(holding.getKey()), offsets.get(holding.getKey()), t);
                cash = cash.add(close.multiply(BigDecimal.valueOf(holding.getValue())));
                iterator.remove();
            }
        }
        return cash;
    }

    private BigDecimal buyEntrants(
            Map<String, BarSeries> eligible,
            Map<String, Integer> offsets,
            Map<String, Long> holdings,
            List<String> target,
            BigDecimal cash,
            int t) {
        List<String> entrants = new ArrayList<>();
        for (String symbol : target) {
            if (!holdings.containsKey(symbol)) {
                entrants.add(symbol);
            }
        }
        if (entrants.isEmpty()) {
            return cash;
        }
        BigDecimal budget = cash.divide(BigDecimal.valueOf(entrants.size()), 10, RoundingMode.DOWN);
        for (String symbol : entrants) {
            BigDecimal close = closeAt(eligible.get(symbol), offsets.get(symbol), t);
            if (close.signum() <= 0) {
                continue;
            }
            long quantity = budget.divideToIntegralValue(close).longValueExact();
            if (quantity > 0L) {
                cash = cash.subtract(close.multiply(BigDecimal.valueOf(quantity)));
                holdings.put(symbol, quantity);
            }
        }
        return cash;
    }

    private BigDecimal closeAt(BarSeries series, int offset, int t) {
        return series.get(offset + t).close();
    }

    private double totalReturnPct(BigDecimal startingCash, List<BigDecimal> equityCurve) {
        if (equityCurve.isEmpty()) {
            return 0.0;
        }
        BigDecimal last = equityCurve.get(equityCurve.size() - 1);
        return last.subtract(startingCash)
                .divide(startingCash, 10, RoundingMode.HALF_UP)
                .doubleValue() * 100.0;
    }

    private double maxDrawdownPct(List<BigDecimal> equityCurve) {
        if (equityCurve.isEmpty()) {
            return 0.0;
        }
        double peak = equityCurve.get(0).doubleValue();
        double maxDrawdown = 0.0;
        for (BigDecimal point : equityCurve) {
            double value = point.doubleValue();
            if (value > peak) {
                peak = value;
            }
            if (peak > 0.0) {
                double drawdown = (peak - value) / peak;
                if (drawdown > maxDrawdown) {
                    maxDrawdown = drawdown;
                }
            }
        }
        return maxDrawdown * 100.0;
    }

    private double sharpe(List<BigDecimal> equityCurve) {
        if (equityCurve.size() < 2) {
            return 0.0;
        }
        List<Double> returns = new ArrayList<>(equityCurve.size() - 1);
        for (int i = 1; i < equityCurve.size(); i++) {
            double prev = equityCurve.get(i - 1).doubleValue();
            double curr = equityCurve.get(i).doubleValue();
            if (prev != 0.0) {
                returns.add((curr - prev) / prev);
            } else {
                returns.add(0.0);
            }
        }
        double mean = 0.0;
        for (double r : returns) {
            mean += r;
        }
        mean /= returns.size();
        double variance = 0.0;
        for (double r : returns) {
            double diff = r - mean;
            variance += diff * diff;
        }
        variance /= returns.size();
        double std = Math.sqrt(variance);
        if (std == 0.0) {
            return 0.0;
        }
        return (mean / std) * Math.sqrt(TRADING_DAYS);
    }
}
