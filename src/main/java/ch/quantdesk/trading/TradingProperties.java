package ch.quantdesk.trading;

import java.util.List;
import java.util.concurrent.atomic.AtomicReference;
import org.springframework.boot.context.properties.ConfigurationProperties;
import org.springframework.stereotype.Component;

@Component
@ConfigurationProperties(prefix = "quantdesk.trading")
public class TradingProperties {

    private final AtomicReference<TradingMode> mode = new AtomicReference<>(TradingMode.OFF);
    private List<String> symbols = List.of();
    private long intervalMs = 60000L;
    private long initialDelayMs = 60000L;
    private Strategy strategy = new Strategy();

    public TradingMode getMode() {
        return mode.get();
    }

    public void setMode(TradingMode mode) {
        this.mode.set(mode);
    }

    public List<String> getSymbols() {
        return symbols;
    }

    public void setSymbols(List<String> symbols) {
        this.symbols = symbols;
    }

    public long getIntervalMs() {
        return intervalMs;
    }

    public void setIntervalMs(long intervalMs) {
        this.intervalMs = intervalMs;
    }

    public long getInitialDelayMs() {
        return initialDelayMs;
    }

    public void setInitialDelayMs(long initialDelayMs) {
        this.initialDelayMs = initialDelayMs;
    }

    public Strategy getStrategy() {
        return strategy;
    }

    public void setStrategy(Strategy strategy) {
        this.strategy = strategy;
    }

    public static class Strategy {

        private int fast = 10;
        private int slow = 30;

        public int getFast() {
            return fast;
        }

        public void setFast(int fast) {
            this.fast = fast;
        }

        public int getSlow() {
            return slow;
        }

        public void setSlow(int slow) {
            this.slow = slow;
        }
    }
}
