package ch.quantdesk.execution;

import ch.quantdesk.backtest.Side;

public record OrderRequest(String symbol, Side side, long quantity) {
}
