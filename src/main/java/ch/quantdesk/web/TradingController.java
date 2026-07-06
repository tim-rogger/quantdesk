package ch.quantdesk.web;

import ch.quantdesk.execution.BrokerGateway;
import ch.quantdesk.execution.Position;
import ch.quantdesk.trading.TradeLog;
import ch.quantdesk.trading.TradeLogEntry;
import ch.quantdesk.trading.TradingEngine;
import ch.quantdesk.trading.TradingMode;
import ch.quantdesk.trading.TradingProperties;
import java.math.BigDecimal;
import java.time.Instant;
import java.util.List;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestParam;
import org.springframework.web.bind.annotation.RestController;

@RestController
public class TradingController {

    private final TradingProperties properties;
    private final TradingEngine engine;
    private final TradeLog tradeLog;
    private final BrokerGateway broker;

    public TradingController(TradingProperties properties,
                             TradingEngine engine,
                             TradeLog tradeLog,
                             BrokerGateway broker) {
        this.properties = properties;
        this.engine = engine;
        this.tradeLog = tradeLog;
        this.broker = broker;
    }

    @GetMapping("/trading/status")
    public TradingStatus status() {
        return new TradingStatus(
                properties.getMode().name(),
                properties.getSymbols(),
                engine.lastCycleAt(),
                broker.cash(),
                engine.equity(),
                broker.isConnected());
    }

    @PostMapping("/trading/mode")
    public TradingStatus mode(@RequestParam TradingMode value) {
        properties.setMode(value);
        return status();
    }

    @PostMapping("/trading/kill")
    public TradingStatus kill() {
        properties.setMode(TradingMode.OFF);
        tradeLog.add(new TradeLogEntry(Instant.now(), "*", "KILL SWITCH", "manual kill switch",
                0L, BigDecimal.ZERO, TradingMode.OFF.name()));
        return status();
    }

    @GetMapping("/trading/log")
    public List<TradeLogEntry> log() {
        return tradeLog.entries();
    }

    @GetMapping("/positions")
    public List<Position> positions() {
        return broker.positions();
    }
}
