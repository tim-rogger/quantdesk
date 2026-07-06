package ch.quantdesk.news;

import com.anthropic.client.AnthropicClient;
import com.anthropic.client.okhttp.AnthropicOkHttpClient;
import com.anthropic.models.messages.Message;
import com.anthropic.models.messages.MessageCreateParams;
import java.util.List;
import java.util.Locale;
import java.util.stream.Collectors;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.boot.autoconfigure.condition.ConditionalOnProperty;
import org.springframework.stereotype.Component;

@Component
@ConditionalOnProperty(name = "quantdesk.sentiment.provider", havingValue = "claude")
public class ClaudeSentimentAnalyzer implements SentimentAnalyzer {

    private final String model;
    private AnthropicClient client;

    public ClaudeSentimentAnalyzer(@Value("${quantdesk.sentiment.model}") String model) {
        this.model = model;
    }

    @Override
    public SentimentScore analyze(List<NewsItem> items) {
        if (items == null || items.isEmpty()) {
            return new SentimentScore(0.0, "no headlines to analyze");
        }
        try {
            MessageCreateParams params = MessageCreateParams.builder()
                    .model(model)
                    .maxTokens(300L)
                    .addUserMessage(buildPrompt(items))
                    .build();
            Message response = anthropicClient().messages().create(params);
            String text = response.content().stream()
                    .flatMap(block -> block.text().stream())
                    .map(textBlock -> textBlock.text())
                    .collect(Collectors.joining("\n"));
            return parseResponse(text);
        } catch (Exception e) {
            return new SentimentScore(0.0, "claude unavailable: " + e.getMessage());
        }
    }

    private synchronized AnthropicClient anthropicClient() {
        if (client == null) {
            client = AnthropicOkHttpClient.fromEnv();
        }
        return client;
    }

    private String buildPrompt(List<NewsItem> items) {
        String symbol = items.get(0).symbol();
        String headlines = items.stream()
                .map(item -> "- " + item.headline())
                .collect(Collectors.joining("\n"));
        return "Rate the overall sentiment of the following news headlines for the stock "
                + symbol
                + ". Respond strictly with \"SCORE: <number between -1 and 1>\" on the first line, "
                + "followed by a one-line rationale on the next line.\n"
                + headlines;
    }

    private SentimentScore parseResponse(String text) {
        String[] lines = text.strip().split("\\R", 2);
        String firstLine = lines[0].strip();
        if (!firstLine.toUpperCase(Locale.ROOT).startsWith("SCORE:")) {
            return new SentimentScore(0.0, "claude unavailable: unexpected response format");
        }
        double score = Double.parseDouble(firstLine.substring("SCORE:".length()).strip());
        score = Math.max(-1.0, Math.min(1.0, score));
        String rationale = lines.length > 1 ? lines[1].strip() : "no rationale provided";
        return new SentimentScore(score, rationale);
    }
}
