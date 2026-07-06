package ch.quantdesk.marketdata;

import java.io.BufferedReader;
import java.io.IOException;
import java.io.InputStream;
import java.io.InputStreamReader;
import java.io.UncheckedIOException;
import java.math.BigDecimal;
import java.nio.charset.StandardCharsets;
import java.time.LocalDate;
import java.util.ArrayList;
import java.util.List;
import org.springframework.core.io.ClassPathResource;
import org.springframework.stereotype.Component;

@Component
public class CsvBarLoader {

    public BarSeries load(String symbol) {
        String path = "data/" + symbol + ".csv";
        ClassPathResource resource = new ClassPathResource(path);
        if (!resource.exists()) {
            throw new IllegalArgumentException("No market data available for symbol: " + symbol);
        }
        List<Bar> bars = new ArrayList<>();
        try (InputStream in = resource.getInputStream();
             BufferedReader reader = new BufferedReader(new InputStreamReader(in, StandardCharsets.UTF_8))) {
            String header = reader.readLine();
            if (header == null) {
                throw new IllegalArgumentException("Empty market data file for symbol: " + symbol);
            }
            String line;
            while ((line = reader.readLine()) != null) {
                if (line.isBlank()) {
                    continue;
                }
                String[] fields = line.split(",");
                if (fields.length < 6) {
                    throw new IllegalArgumentException("Malformed row in market data for symbol " + symbol + ": " + line);
                }
                Bar bar = new Bar(
                        LocalDate.parse(fields[0].trim()),
                        new BigDecimal(fields[1].trim()),
                        new BigDecimal(fields[2].trim()),
                        new BigDecimal(fields[3].trim()),
                        new BigDecimal(fields[4].trim()),
                        Long.parseLong(fields[5].trim()));
                bars.add(bar);
            }
        } catch (IOException e) {
            throw new UncheckedIOException("Failed to read market data for symbol: " + symbol, e);
        }
        return new BarSeries(symbol, bars);
    }
}
