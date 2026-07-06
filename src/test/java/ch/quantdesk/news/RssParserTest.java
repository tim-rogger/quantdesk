package ch.quantdesk.news;

import static org.assertj.core.api.Assertions.assertThat;

import java.time.Instant;
import java.util.List;
import org.junit.jupiter.api.Test;

class RssParserTest {

    private static final String FEED = """
            <?xml version="1.0" encoding="UTF-8"?>
            <rss version="2.0">
              <channel>
                <title>Yahoo! Finance: AAPL News</title>
                <item>
                  <title>Apple beats earnings expectations</title>
                  <link>https://example.com/apple-beats</link>
                  <pubDate>Mon, 29 Jun 2026 14:30:00 GMT</pubDate>
                </item>
                <item>
                  <title>Apple faces lawsuit over patents</title>
                  <link>https://example.com/apple-lawsuit</link>
                  <pubDate>not-a-valid-date</pubDate>
                </item>
                <item>
                  <title>Apple stock rally continues</title>
                  <link>https://example.com/apple-rally</link>
                  <pubDate>Tue, 30 Jun 2026 09:15:00 +0000</pubDate>
                </item>
              </channel>
            </rss>
            """;

    @Test
    void parsesAllItemsWithSymbolHeadlineAndUrl() {
        List<NewsItem> items = RssParser.parse("AAPL", FEED);

        assertThat(items).hasSize(3);
        assertThat(items).allSatisfy(item -> {
            assertThat(item.symbol()).isEqualTo("AAPL");
            assertThat(item.publishedAt()).isNotNull();
        });
        assertThat(items.get(0).headline()).isEqualTo("Apple beats earnings expectations");
        assertThat(items.get(0).url()).isEqualTo("https://example.com/apple-beats");
        assertThat(items.get(2).headline()).isEqualTo("Apple stock rally continues");
        assertThat(items.get(2).url()).isEqualTo("https://example.com/apple-rally");
    }

    @Test
    void parsesRfc1123PubDates() {
        List<NewsItem> items = RssParser.parse("AAPL", FEED);

        assertThat(items.get(0).publishedAt()).isEqualTo(Instant.parse("2026-06-29T14:30:00Z"));
        assertThat(items.get(2).publishedAt()).isEqualTo(Instant.parse("2026-06-30T09:15:00Z"));
    }

    @Test
    void fallsBackToNowOnUnparseablePubDate() {
        Instant before = Instant.now();
        List<NewsItem> items = RssParser.parse("AAPL", FEED);
        Instant after = Instant.now();

        assertThat(items.get(1).publishedAt()).isBetween(before, after);
    }

    @Test
    void returnsEmptyListOnMalformedXml() {
        assertThat(RssParser.parse("AAPL", "not xml at all")).isEmpty();
    }
}
