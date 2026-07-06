package ch.quantdesk.trading;

import java.util.ArrayDeque;
import java.util.Deque;
import java.util.List;
import org.springframework.stereotype.Component;

@Component
public class TradeLog {

    private static final int MAX_ENTRIES = 500;

    private final Deque<TradeLogEntry> entries = new ArrayDeque<>(MAX_ENTRIES);

    public synchronized void add(TradeLogEntry entry) {
        if (entries.size() >= MAX_ENTRIES) {
            entries.removeFirst();
        }
        entries.addLast(entry);
    }

    public synchronized List<TradeLogEntry> entries() {
        return List.copyOf(entries);
    }
}
