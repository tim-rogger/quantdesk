package ch.quantdesk.news;

import java.util.List;
import java.util.Locale;
import java.util.Set;
import org.springframework.boot.autoconfigure.condition.ConditionalOnProperty;
import org.springframework.stereotype.Component;

@Component
@ConditionalOnProperty(name = "quantdesk.sentiment.provider", havingValue = "lexicon", matchIfMissing = true)
public class LexiconSentimentAnalyzer implements SentimentAnalyzer {

    private static final Set<String> POSITIVE = Set.of(
            "beat", "beats", "surge", "record", "upgrade", "growth",
            "profit", "rally", "strong", "bullish", "wins", "soars");

    private static final Set<String> NEGATIVE = Set.of(
            "miss", "misses", "plunge", "downgrade", "loss", "lawsuit",
            "recall", "weak", "bearish", "fraud", "cuts", "falls");

    @Override
    public SentimentScore analyze(List<NewsItem> items) {
        if (items == null || items.isEmpty()) {
            return new SentimentScore(0.0, "no headlines to analyze");
        }
        double total = 0.0;
        int positiveHits = 0;
        int negativeHits = 0;
        for (NewsItem item : items) {
            int pos = 0;
            int neg = 0;
            for (String word : item.headline().toLowerCase(Locale.ROOT).split("[^a-z]+")) {
                if (POSITIVE.contains(word)) {
                    pos++;
                } else if (NEGATIVE.contains(word)) {
                    neg++;
                }
            }
            positiveHits += pos;
            negativeHits += neg;
            total += (pos - neg) / (double) Math.max(1, pos + neg);
        }
        double score = total / items.size();
        String rationale = "%d positive and %d negative signals across %d headlines"
                .formatted(positiveHits, negativeHits, items.size());
        return new SentimentScore(score, rationale);
    }
}
