package ch.quantdesk.news;

import static org.assertj.core.api.Assertions.assertThat;

import java.time.Instant;
import java.util.List;
import org.junit.jupiter.api.Test;

class LexiconSentimentAnalyzerTest {

    private final LexiconSentimentAnalyzer analyzer = new LexiconSentimentAnalyzer();

    private NewsItem item(String headline) {
        return new NewsItem("AAPL", headline, Instant.now(), "https://example.com/news");
    }

    @Test
    void positiveHeadlinesScoreAboveZero() {
        SentimentScore score = analyzer.analyze(List.of(
                item("Apple beats expectations as profit soars"),
                item("Strong growth fuels record rally")));

        assertThat(score.score()).isGreaterThan(0.0);
        assertThat(score.score()).isLessThanOrEqualTo(1.0);
        assertThat(score.rationale()).isNotBlank();
    }

    @Test
    void negativeHeadlinesScoreBelowZero() {
        SentimentScore score = analyzer.analyze(List.of(
                item("Apple misses estimates as weak demand cuts revenue"),
                item("Lawsuit and recall deepen quarterly loss")));

        assertThat(score.score()).isLessThan(0.0);
        assertThat(score.score()).isGreaterThanOrEqualTo(-1.0);
    }

    @Test
    void mixedHeadlinesStayWithinBounds() {
        SentimentScore score = analyzer.analyze(List.of(
                item("Apple beats on profit but faces lawsuit"),
                item("Weak outlook despite strong quarter")));

        assertThat(score.score()).isBetween(-1.0, 1.0);
    }

    @Test
    void headlinesWithoutSignalWordsScoreZero() {
        SentimentScore score = analyzer.analyze(List.of(
                item("Apple schedules annual developer conference")));

        assertThat(score.score()).isZero();
    }

    @Test
    void emptyListScoresZero() {
        SentimentScore score = analyzer.analyze(List.of());

        assertThat(score.score()).isZero();
        assertThat(score.rationale()).isNotBlank();
    }
}
