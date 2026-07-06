package ch.quantdesk.backtest.metrics;

import java.math.BigDecimal;
import java.util.List;

public final class PerformanceMetrics {

    private static final double DEFAULT_TRADING_DAYS = 252.0;

    private PerformanceMetrics() {
    }

    public static double cagr(List<BigDecimal> equityCurve, double tradingDaysPerYear) {
        if (equityCurve == null || equityCurve.size() < 2 || tradingDaysPerYear <= 0.0) {
            return 0.0;
        }
        double start = equityCurve.get(0).doubleValue();
        double end = equityCurve.get(equityCurve.size() - 1).doubleValue();
        if (start <= 0.0 || end <= 0.0) {
            return 0.0;
        }
        double periods = equityCurve.size() - 1;
        double years = periods / tradingDaysPerYear;
        if (years <= 0.0) {
            return 0.0;
        }
        return (Math.pow(end / start, 1.0 / years) - 1.0) * 100.0;
    }

    public static double annualizedVolatility(List<BigDecimal> equityCurve) {
        double[] returns = periodicReturns(equityCurve);
        if (returns.length < 2) {
            return 0.0;
        }
        double std = standardDeviation(returns, mean(returns));
        return std * Math.sqrt(DEFAULT_TRADING_DAYS) * 100.0;
    }

    public static double sortino(List<BigDecimal> equityCurve) {
        double[] returns = periodicReturns(equityCurve);
        if (returns.length < 2) {
            return 0.0;
        }
        double mean = mean(returns);
        double downsideSumSquares = 0.0;
        for (double r : returns) {
            if (r < 0.0) {
                downsideSumSquares += r * r;
            }
        }
        double downsideDeviation = Math.sqrt(downsideSumSquares / returns.length);
        if (downsideDeviation == 0.0) {
            return 0.0;
        }
        return (mean / downsideDeviation) * Math.sqrt(DEFAULT_TRADING_DAYS);
    }

    public static double calmar(List<BigDecimal> equityCurve) {
        double cagr = cagr(equityCurve, DEFAULT_TRADING_DAYS);
        double maxDrawdown = maxDrawdownPct(equityCurve);
        if (maxDrawdown == 0.0) {
            return 0.0;
        }
        return cagr / maxDrawdown;
    }

    private static double[] periodicReturns(List<BigDecimal> equityCurve) {
        if (equityCurve == null || equityCurve.size() < 2) {
            return new double[0];
        }
        double[] returns = new double[equityCurve.size() - 1];
        for (int i = 1; i < equityCurve.size(); i++) {
            double prev = equityCurve.get(i - 1).doubleValue();
            double curr = equityCurve.get(i).doubleValue();
            returns[i - 1] = prev != 0.0 ? (curr - prev) / prev : 0.0;
        }
        return returns;
    }

    private static double mean(double[] values) {
        double sum = 0.0;
        for (double v : values) {
            sum += v;
        }
        return sum / values.length;
    }

    private static double standardDeviation(double[] values, double mean) {
        double variance = 0.0;
        for (double v : values) {
            double diff = v - mean;
            variance += diff * diff;
        }
        variance /= values.length;
        return Math.sqrt(variance);
    }

    private static double maxDrawdownPct(List<BigDecimal> equityCurve) {
        if (equityCurve == null || equityCurve.isEmpty()) {
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
}
