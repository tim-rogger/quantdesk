package ch.quantdesk.news;

import java.util.List;

public interface SentimentAnalyzer {

    SentimentScore analyze(List<NewsItem> items);
}
