package ch.quantdesk.news;

import java.util.List;

public record NewsResponse(String symbol, List<NewsItem> items, SentimentScore sentiment) {
}
