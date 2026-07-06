package ch.quantdesk.universe;

import java.io.BufferedReader;
import java.io.IOException;
import java.io.InputStream;
import java.io.InputStreamReader;
import java.io.UncheckedIOException;
import java.nio.charset.StandardCharsets;
import java.util.ArrayList;
import java.util.List;
import org.springframework.core.io.ClassPathResource;
import org.springframework.stereotype.Component;

@Component
public class CsvUniverseProvider implements UniverseProvider {

    private final UniverseProperties properties;

    public CsvUniverseProvider(UniverseProperties properties) {
        this.properties = properties;
    }

    @Override
    public List<String> symbols() {
        String path = properties.getFile();
        ClassPathResource resource = new ClassPathResource(path);
        if (!resource.exists()) {
            throw new IllegalArgumentException("No universe file available: " + path);
        }
        List<String> symbols = new ArrayList<>();
        try (InputStream in = resource.getInputStream();
             BufferedReader reader = new BufferedReader(new InputStreamReader(in, StandardCharsets.UTF_8))) {
            String header = reader.readLine();
            if (header == null) {
                return List.of();
            }
            String line;
            while ((line = reader.readLine()) != null) {
                if (line.isBlank()) {
                    continue;
                }
                String symbol = line.split(",")[0].trim();
                if (symbol.isEmpty()) {
                    continue;
                }
                symbols.add(symbol.toLowerCase());
            }
        } catch (IOException e) {
            throw new UncheckedIOException("Failed to read universe file: " + path, e);
        }
        return List.copyOf(symbols);
    }
}
