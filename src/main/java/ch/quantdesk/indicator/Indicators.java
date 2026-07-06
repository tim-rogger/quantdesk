package ch.quantdesk.indicator;

import java.math.BigDecimal;
import java.math.MathContext;
import java.math.RoundingMode;
import java.util.ArrayList;
import java.util.List;

public final class Indicators {

    private static final MathContext MC = new MathContext(16, RoundingMode.HALF_UP);

    private Indicators() {
    }

    public static List<BigDecimal> sma(List<BigDecimal> values, int period) {
        if (period <= 0) {
            throw new IllegalArgumentException("SMA period must be positive: " + period);
        }
        List<BigDecimal> result = new ArrayList<>(values.size());
        BigDecimal divisor = BigDecimal.valueOf(period);
        for (int i = 0; i < values.size(); i++) {
            if (i < period - 1) {
                result.add(null);
                continue;
            }
            BigDecimal sum = BigDecimal.ZERO;
            for (int j = i - period + 1; j <= i; j++) {
                sum = sum.add(values.get(j));
            }
            result.add(sum.divide(divisor, MC));
        }
        return result;
    }

    public static List<Double> rsi(List<BigDecimal> values, int period) {
        if (period <= 0) {
            throw new IllegalArgumentException("RSI period must be positive: " + period);
        }
        List<Double> result = new ArrayList<>(values.size());
        for (int i = 0; i < values.size(); i++) {
            result.add(null);
        }
        if (values.size() <= period) {
            return result;
        }
        double gainSum = 0.0;
        double lossSum = 0.0;
        for (int i = 1; i <= period; i++) {
            double change = values.get(i).subtract(values.get(i - 1)).doubleValue();
            if (change >= 0) {
                gainSum += change;
            } else {
                lossSum -= change;
            }
        }
        double avgGain = gainSum / period;
        double avgLoss = lossSum / period;
        result.set(period, computeRsi(avgGain, avgLoss));
        for (int i = period + 1; i < values.size(); i++) {
            double change = values.get(i).subtract(values.get(i - 1)).doubleValue();
            double gain = change >= 0 ? change : 0.0;
            double loss = change < 0 ? -change : 0.0;
            avgGain = (avgGain * (period - 1) + gain) / period;
            avgLoss = (avgLoss * (period - 1) + loss) / period;
            result.set(i, computeRsi(avgGain, avgLoss));
        }
        return result;
    }

    private static double computeRsi(double avgGain, double avgLoss) {
        if (avgLoss == 0.0) {
            return 100.0;
        }
        double rs = avgGain / avgLoss;
        return 100.0 - (100.0 / (1.0 + rs));
    }
}
