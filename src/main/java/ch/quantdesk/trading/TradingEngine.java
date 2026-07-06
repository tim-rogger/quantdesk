package ch.quantdesk.trading;

import ch.quantdesk.backtest.Side;
import ch.quantdesk.execution.BrokerGateway;
import ch.quantdesk.execution.OrderRequest;
import ch.quantdesk.execution.OrderResult;
import ch.quantdesk.execution.Position;
import ch.quantdesk.marketdata.BarSeries;
import ch.quantdesk.marketdata.MarketDataProvider;
import ch.quantdesk.news.NewsProvider;
import ch.quantdesk.news.SentimentAnalyzer;
import ch.quantdesk.news.SentimentScore;
import ch.quantdesk.risk.PositionSizer;
import ch.quantdesk.strategy.Signal;
import ch.quantdesk.strategy.SmaCrossoverStrategy;
import ch.quantdesk.universe.UniverseScanner;
import java.math.BigDecimal;
import java.time.Instant;
import java.time.LocalDate;
import java.util.HashSet;
import java.util.List;
import java.util.Optional;
import java.util.Set;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.scheduling.annotation.Scheduled;
import org.springframework.stereotype.Service;

@Service
public class TradingEngine {

    private final MarketDataProvider marketData;
    private final BrokerGateway broker;
    private final TradingProperties tradingProperties;
    private final RiskProperties riskProperties;
    private final RiskManager riskManager;
    private final TradeLog tradeLog;
    private final UniverseScanner universeScanner;
    private final Optional<NewsProvider> newsProvider;
    private final Optional<SentimentAnalyzer> sentimentAnalyzer;
    private final boolean universeMode;

    private volatile Instant lastCycleAt;
    private volatile LocalDate currentDay;
    private volatile BigDecimal dayStartEquity;

    public TradingEngine(MarketDataProvider marketData,
                         BrokerGateway broker,
                         TradingProperties tradingProperties,
                         RiskProperties riskProperties,
                         RiskManager riskManager,
                         TradeLog tradeLog,
                         UniverseScanner universeScanner,
                         Optional<NewsProvider> newsProvider,
                         Optional<SentimentAnalyzer> sentimentAnalyzer,
                         @Value("${quantdesk.trading.universe-mode}") boolean universeMode) {
        this.marketData = marketData;
        this.broker = broker;
        this.tradingProperties = tradingProperties;
        this.riskProperties = riskProperties;
        this.riskManager = riskManager;
        this.tradeLog = tradeLog;
        this.universeScanner = universeScanner;
        this.newsProvider = newsProvider;
        this.sentimentAnalyzer = sentimentAnalyzer;
        this.universeMode = universeMode;
    }

    @Scheduled(fixedDelayString = "${quantdesk.trading.interval-ms}",
            initialDelayString = "${quantdesk.trading.initial-delay-ms}")
    public void cycle() {
        if (tradingProperties.getMode() == TradingMode.OFF) {
            return;
        }
        rollDay();
        if (universeMode) {
            rebalanceCycle();
        } else {
            watchlistCycle();
        }
        lastCycleAt = Instant.now();
    }

    public Instant lastCycleAt() {
        return lastCycleAt;
    }

    public BigDecimal equity() {
        BigDecimal equity = broker.cash();
        for (Position position : broker.positions()) {
            BigDecimal value = marketData.lastPrice(position.symbol())
                    .multiply(BigDecimal.valueOf(position.quantity()));
            equity = equity.add(value);
        }
        return equity;
    }

    private void watchlistCycle() {
        for (String symbol : tradingProperties.getSymbols()) {
            try {
                evaluate(symbol);
            } catch (Exception e) {
                log(symbol, "ERROR", messageOf(e), 0L, BigDecimal.ZERO, tradingProperties.getMode());
            }
        }
    }

    private void rebalanceCycle() {
        TradingMode mode = tradingProperties.getMode();
        List<String> targets;
        try {
            if (universeScanner.getLastScan().isEmpty()) {
                universeScanner.scan();
            }
            targets = universeScanner.topSymbols();
        } catch (Exception e) {
            log("*", "ERROR", messageOf(e), 0L, BigDecimal.ZERO, mode);
            return;
        }
        Set<String> held = new HashSet<>();
        for (Position position : broker.positions()) {
            held.add(position.symbol());
            try {
                if (targets.contains(position.symbol())) {
                    log(position.symbol(), "KEEP", "still in target universe",
                            position.quantity(), position.avgPrice(), mode);
                } else {
                    exitPosition(position, mode);
                }
            } catch (Exception e) {
                log(position.symbol(), "ERROR", messageOf(e), 0L, BigDecimal.ZERO, mode);
            }
        }
        for (String symbol : targets) {
            if (held.contains(symbol)) {
                continue;
            }
            try {
                enterTarget(symbol, targets.size(), mode);
            } catch (Exception e) {
                log(symbol, "ERROR", messageOf(e), 0L, BigDecimal.ZERO, mode);
            }
        }
    }

    private void exitPosition(Position position, TradingMode mode) {
        BigDecimal price = marketData.lastPrice(position.symbol());
        if (mode == TradingMode.DRY_RUN) {
            log(position.symbol(), "DRY_SELL", "left target universe", position.quantity(), price, mode);
            return;
        }
        OrderRequest request = new OrderRequest(position.symbol(), Side.SELL, position.quantity());
        OrderResult result = broker.placeOrder(request);
        log(position.symbol(), "SELL", "left target universe -> " + result.status(),
                position.quantity(), price, mode);
    }

    private void enterTarget(String symbol, int topN, TradingMode mode) {
        BigDecimal price = marketData.lastPrice(symbol);
        double fraction = Math.min(1.0 / topN, riskProperties.getMaxPositionFraction());
        long quantity = PositionSizer.fixedFraction(broker.cash(), price, fraction);
        if (quantity <= 0L) {
            log(symbol, "HOLD", "insufficient cash", 0L, price, mode);
            return;
        }
        if (newsProvider.isPresent() && sentimentAnalyzer.isPresent()) {
            SentimentScore sentiment = sentimentAnalyzer.get().analyze(newsProvider.get().latest(symbol));
            if (sentiment.score() < -0.5) {
                log(symbol, "VETO", "negative news", quantity, price, mode);
                return;
            }
        }
        if (mode == TradingMode.DRY_RUN) {
            log(symbol, "DRY_BUY", "entered target universe", quantity, price, mode);
            return;
        }
        OrderRequest request = new OrderRequest(symbol, Side.BUY, quantity);
        if (riskManager.allowOrder(request, price, broker.cash(), equity(), dayStartEquity)) {
            OrderResult result = broker.placeOrder(request);
            log(symbol, "BUY", "entered target universe -> " + result.status(), quantity, price, mode);
        } else {
            log(symbol, "BLOCKED", riskManager.lastRejectReason(), quantity, price, mode);
        }
    }

    private void rollDay() {
        LocalDate today = LocalDate.now();
        if (today.equals(currentDay)) {
            return;
        }
        try {
            dayStartEquity = equity();
            currentDay = today;
        } catch (Exception e) {
            log("*", "ERROR", messageOf(e), 0L, BigDecimal.ZERO, tradingProperties.getMode());
        }
    }

    private void evaluate(String symbol) {
        TradingMode mode = tradingProperties.getMode();
        if (mode == TradingMode.OFF) {
            return;
        }
        BarSeries series = marketData.history(symbol);
        SmaCrossoverStrategy strategy = new SmaCrossoverStrategy(
                tradingProperties.getStrategy().getFast(),
                tradingProperties.getStrategy().getSlow());
        Signal signal = strategy.decide(series, series.size() - 1);
        BigDecimal price = marketData.lastPrice(symbol);
        Position position = findPosition(symbol);
        String reason = strategy.name() + " " + signal;
        if (newsProvider.isPresent() && sentimentAnalyzer.isPresent()) {
            SentimentScore sentiment = sentimentAnalyzer.get().analyze(newsProvider.get().latest(symbol));
            if (signal == Signal.BUY && sentiment.score() < -0.5) {
                signal = Signal.HOLD;
                reason = "negative news";
            } else if (signal == Signal.HOLD && sentiment.score() > 0.5 && position == null) {
                reason = "positive news, no technical signal";
            }
        }
        if (signal == Signal.BUY && position == null) {
            long quantity = PositionSizer.fixedFraction(broker.cash(), price,
                    riskProperties.getMaxPositionFraction());
            if (quantity <= 0L) {
                log(symbol, "HOLD", "insufficient cash", 0L, price, mode);
                return;
            }
            execute(new OrderRequest(symbol, Side.BUY, quantity), price, reason, mode);
            return;
        }
        if (signal == Signal.SELL && position != null) {
            execute(new OrderRequest(symbol, Side.SELL, position.quantity()), price, reason, mode);
            return;
        }
        if (signal == Signal.BUY) {
            log(symbol, "HOLD", "position already open", 0L, price, mode);
            return;
        }
        if (signal == Signal.SELL) {
            log(symbol, "HOLD", "no position to sell", 0L, price, mode);
            return;
        }
        log(symbol, "HOLD", reason, 0L, price, mode);
    }

    private void execute(OrderRequest request, BigDecimal price, String reason, TradingMode mode) {
        if (mode == TradingMode.DRY_RUN) {
            log(request.symbol(), "DRY_" + request.side(), reason, request.quantity(), price, mode);
            return;
        }
        if (riskManager.allowOrder(request, price, broker.cash(), equity(), dayStartEquity)) {
            OrderResult result = broker.placeOrder(request);
            log(request.symbol(), request.side().name(), reason + " -> " + result.status(),
                    request.quantity(), price, mode);
        } else {
            log(request.symbol(), "BLOCKED", riskManager.lastRejectReason(), request.quantity(), price, mode);
        }
    }

    private Position findPosition(String symbol) {
        for (Position position : broker.positions()) {
            if (position.symbol().equals(symbol)) {
                return position;
            }
        }
        return null;
    }

    private void log(String symbol, String action, String reason, long quantity, BigDecimal price, TradingMode mode) {
        tradeLog.add(new TradeLogEntry(Instant.now(), symbol, action, reason, quantity, price, mode.name()));
    }

    private String messageOf(Exception e) {
        return e.getMessage() != null ? e.getMessage() : e.getClass().getSimpleName();
    }
}
