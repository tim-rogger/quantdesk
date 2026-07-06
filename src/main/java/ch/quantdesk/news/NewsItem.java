package ch.quantdesk.news;

import java.time.Instant;

public record NewsItem(String symbol, String headline, Instant publishedAt, String url) {
}
