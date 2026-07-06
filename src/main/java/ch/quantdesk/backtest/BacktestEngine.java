package ch.quantdesk.backtest;

import ch.quantdesk.marketdata.Bar;
import ch.quantdesk.marketdata.BarSeries;
import ch.quantdesk.strategy.Signal;
import ch.quantdesk.strategy.Strategy;
import java.math.BigDecimal;
import java.math.RoundingMode;
import java.util.ArrayList;
import java.util.List;
import org.springframework.stereotype.Service;

@Service
public class BacktestEngine {

    private static final double TRADING_DAYS = 252.0;

    public BacktestResult run(BarSeries series, Strategy strategy, BigDecimal startingCash) {
        if (series == null || strategy == null || startingCash == null) {
            throw new IllegalArgumentException("series, strategy and startingCash must not be null");
        }
        if (startingCash.signum() <= 0) {
            throw new IllegalArgumentException("startingCash must be positive");
        }

        BigDecimal cash = startingCash;
        long shares = 0L;
        BigDecimal entryPrice = null;
        List<BigDecimal> equityCurve = new ArrayList<>(series.size());
        List<Trade> trades = new ArrayList<>();
        int closedRoundTrips = 0;
        int winningRoundTrips = 0;

        for (int i = 0; i < series.size(); i++) {
            Bar bar = series.get(i);
            BigDecimal close = bar.close();
            Signal signal = strategy.decide(series, i);

            if (signal == Signal.BUY && shares == 0L) {
                long quantity = cash.divideToIntegralValue(close).longValueExact();
                if (quantity > 0L) {
                    BigDecimal cost = close.multiply(BigDecimal.valueOf(quantity));
                    cash = cash.subtract(cost);
                    shares = quantity;
                    entryPrice = close;
                    trades.add(new Trade(bar.date(), Side.BUY, close, quantity));
                }
            } else if (signal == Signal.SELL && shares > 0L) {
                BigDecimal proceeds = close.multiply(BigDecimal.valueOf(shares));
                cash = cash.add(proceeds);
                trades.add(new Trade(bar.date(), Side.SELL, close, shares));
                closedRoundTrips++;
                if (entryPrice != null && close.compareTo(entryPrice) > 0) {
                    winningRoundTrips++;
                }
                shares = 0L;
                entryPrice = null;
            }

            BigDecimal equity = cash.add(close.multiply(BigDecimal.valueOf(shares)));
            equityCurve.add(equity);
        }

        double totalReturnPct = totalReturnPct(startingCash, equityCurve);
        double maxDrawdownPct = maxDrawdownPct(equityCurve);
        double sharpe = sharpe(equityCurve);
        double winRatePct = closedRoundTrips == 0 ? 0.0 : (winningRoundTrips * 100.0) / closedRoundTrips;

        return new BacktestResult(
                series.symbol(),
                strategy.name(),
                equityCurve,
                trades,
                totalReturnPct,
                maxDrawdownPct,
                sharpe,
                trades.size(),
                winRatePct);
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
