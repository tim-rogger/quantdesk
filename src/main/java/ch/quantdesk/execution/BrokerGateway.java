package ch.quantdesk.execution;

import java.math.BigDecimal;
import java.util.List;

public interface BrokerGateway {

    OrderResult placeOrder(OrderRequest request);

    List<Position> positions();

    BigDecimal cash();

    boolean isConnected();
}
