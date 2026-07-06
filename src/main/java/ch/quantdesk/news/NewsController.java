package ch.quantdesk.news;

import java.util.List;
import java.util.Optional;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PathVariable;
import org.springframework.web.bind.annotation.RestController;

@RestController
public class NewsController {

    private final Optional<NewsProvider> newsProvider;
    private final SentimentAnalyzer sentimentAnalyzer;

    public NewsController(Optional<NewsProvider> newsProvider, SentimentAnalyzer sentimentAnalyzer) {
        this.newsProvider = newsProvider;
        this.sentimentAnalyzer = sentimentAnalyzer;
    }

    @GetMapping("/news/{symbol}")
    public NewsResponse news(@PathVariable String symbol) {
        if (newsProvider.isEmpty()) {
            return new NewsResponse(symbol, List.of(), new SentimentScore(0.0, "news disabled"));
        }
        List<NewsItem> items = newsProvider.get().latest(symbol);
        SentimentScore sentiment = sentimentAnalyzer.analyze(items);
        return new NewsResponse(symbol, items, sentiment);
    }
}
