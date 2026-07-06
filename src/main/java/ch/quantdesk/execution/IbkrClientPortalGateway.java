package ch.quantdesk.execution;

import com.fasterxml.jackson.core.JsonProcessingException;
import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import java.math.BigDecimal;
import java.net.http.HttpClient;
import java.security.GeneralSecurityException;
import java.security.SecureRandom;
import java.security.cert.X509Certificate;
import java.util.ArrayList;
import java.util.List;
import java.util.Map;
import javax.net.ssl.SSLContext;
import javax.net.ssl.TrustManager;
import javax.net.ssl.X509TrustManager;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.boot.autoconfigure.condition.ConditionalOnProperty;
import org.springframework.http.client.JdkClientHttpRequestFactory;
import org.springframework.stereotype.Component;
import org.springframework.web.client.RestClient;

@Component
@ConditionalOnProperty(name = "quantdesk.ibkr.enabled", havingValue = "true")
public class IbkrClientPortalGateway implements BrokerGateway {

    private final String baseUrl;
    private final String accountId;
    private final ObjectMapper objectMapper = new ObjectMapper();
    private volatile RestClient restClient;

    public IbkrClientPortalGateway(@Value("${quantdesk.ibkr.base-url}") String baseUrl,
                                   @Value("${quantdesk.ibkr.account-id}") String accountId) {
        this.baseUrl = baseUrl;
        this.accountId = accountId;
    }

    @Override
    public OrderResult placeOrder(OrderRequest request) {
        try {
            long conid = resolveConid(request.symbol());
            Map<String, Object> order = Map.of(
                    "conid", conid,
                    "orderType", "MKT",
                    "side", request.side().name(),
                    "quantity", request.quantity(),
                    "tif", "DAY");
            String response = client().post()
                    .uri("/iserver/account/{accountId}/orders", accountId)
                    .body(Map.of("orders", List.of(order)))
                    .retrieve()
                    .body(String.class);
            JsonNode node = objectMapper.readTree(response);
            JsonNode first = node.isArray() && !node.isEmpty() ? node.get(0) : node;
            String orderId = first.path("order_id").asText("N/A");
            String status = first.path("order_status").asText("SUBMITTED");
            return new OrderResult(orderId, status, BigDecimal.ZERO);
        } catch (Exception e) {
            return new OrderResult("N/A", "ERROR: " + e.getMessage(), BigDecimal.ZERO);
        }
    }

    @Override
    public List<Position> positions() {
        try {
            String response = client().get()
                    .uri("/portfolio/{accountId}/positions/0", accountId)
                    .retrieve()
                    .body(String.class);
            JsonNode node = objectMapper.readTree(response);
            List<Position> result = new ArrayList<>();
            if (node.isArray()) {
                for (JsonNode entry : node) {
                    String symbol = entry.path("contractDesc").asText("");
                    if (symbol.isEmpty()) {
                        symbol = entry.path("ticker").asText("");
                    }
                    long quantity = entry.path("position").asLong();
                    JsonNode avgCost = entry.path("avgCost");
                    BigDecimal avgPrice = avgCost.isNumber() ? avgCost.decimalValue() : BigDecimal.ZERO;
                    result.add(new Position(symbol, quantity, avgPrice));
                }
            }
            return result;
        } catch (Exception e) {
            return List.of();
        }
    }

    @Override
    public BigDecimal cash() {
        try {
            String response = client().get()
                    .uri("/portfolio/{accountId}/ledger", accountId)
                    .retrieve()
                    .body(String.class);
            JsonNode cashBalance = objectMapper.readTree(response).path("BASE").path("cashbalance");
            return cashBalance.isNumber() ? cashBalance.decimalValue() : BigDecimal.ZERO;
        } catch (Exception e) {
            return BigDecimal.ZERO;
        }
    }

    @Override
    public boolean isConnected() {
        try {
            client().get()
                    .uri("/iserver/accounts")
                    .retrieve()
                    .body(String.class);
            return true;
        } catch (Exception e) {
            return false;
        }
    }

    private long resolveConid(String symbol) throws JsonProcessingException {
        String response = client().get()
                .uri("/iserver/secdef/search?symbol={symbol}", symbol)
                .retrieve()
                .body(String.class);
        JsonNode node = objectMapper.readTree(response);
        if (!node.isArray() || node.isEmpty()) {
            throw new IllegalStateException("No contract found for symbol: " + symbol);
        }
        return node.get(0).path("conid").asLong();
    }

    private RestClient client() {
        RestClient client = restClient;
        if (client == null) {
            synchronized (this) {
                if (restClient == null) {
                    restClient = buildClient();
                }
                client = restClient;
            }
        }
        return client;
    }

    private RestClient buildClient() {
        try {
            SSLContext sslContext = SSLContext.getInstance("TLS");
            sslContext.init(null, new TrustManager[]{new X509TrustManager() {
                @Override
                public void checkClientTrusted(X509Certificate[] chain, String authType) {
                }

                @Override
                public void checkServerTrusted(X509Certificate[] chain, String authType) {
                }

                @Override
                public X509Certificate[] getAcceptedIssuers() {
                    return new X509Certificate[0];
                }
            }}, new SecureRandom());
            HttpClient httpClient = HttpClient.newBuilder()
                    .sslContext(sslContext)
                    .build();
            return RestClient.builder()
                    .baseUrl(baseUrl)
                    .requestFactory(new JdkClientHttpRequestFactory(httpClient))
                    .build();
        } catch (GeneralSecurityException e) {
            throw new IllegalStateException("Failed to initialise IBKR client", e);
        }
    }
}
