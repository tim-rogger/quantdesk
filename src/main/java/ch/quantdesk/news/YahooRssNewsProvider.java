package ch.quantdesk.news;

import java.util.List;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.boot.autoconfigure.condition.ConditionalOnProperty;
import org.springframework.stereotype.Component;
import org.springframework.web.client.RestClient;

@Component
@ConditionalOnProperty(name = "quantdesk.news.enabled", havingValue = "true")
public class YahooRssNewsProvider implements NewsProvider {

    private static final Logger log = LoggerFactory.getLogger(YahooRssNewsProvider.class);
    private static final String FEED_URL =
            "https://feeds.finance.yahoo.com/rss/2.0/headline?s={symbol}&region=US&lang=en-US";

    private RestClient restClient;

    @Override
    public List<NewsItem> latest(String symbol) {
        try {
            String rssXml = restClient()
                    .get()
                    .uri(FEED_URL, symbol)
                    .retrieve()
                    .body(String.class);
            if (rssXml == null) {
                return List.of();
            }
            return RssParser.parse(symbol, rssXml);
        } catch (Exception e) {
            log.warn("Failed to fetch news feed for {}: {}", symbol, e.getMessage());
            return List.of();
        }
    }

    private synchronized RestClient restClient() {
        if (restClient == null) {
            restClient = RestClient.create();
        }
        return restClient;
    }
}
