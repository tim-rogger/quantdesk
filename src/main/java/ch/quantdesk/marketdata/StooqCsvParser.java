package ch.quantdesk.marketdata;

import java.math.BigDecimal;
import java.time.LocalDate;
import java.time.format.DateTimeParseException;
import java.util.ArrayList;
import java.util.Comparator;
import java.util.List;

public final class StooqCsvParser {

    private StooqCsvParser() {
    }

    public static BarSeries parse(String symbol, String csv) {
        List<Bar> bars = new ArrayList<>();
        String[] lines = csv.split("\\R");
        for (int i = 1; i < lines.length; i++) {
            String line = lines[i];
            if (line.isBlank()) {
                continue;
            }
            String[] fields = line.split(",");
            if (fields.length < 6) {
                continue;
            }
            try {
                bars.add(new Bar(
                        LocalDate.parse(fields[0].trim()),
                        new BigDecimal(fields[1].trim()),
                        new BigDecimal(fields[2].trim()),
                        new BigDecimal(fields[3].trim()),
                        new BigDecimal(fields[4].trim()),
                        Long.parseLong(fields[5].trim())));
            } catch (DateTimeParseException | NumberFormatException e) {
                continue;
            }
        }
        bars.sort(Comparator.comparing(Bar::date));
        return new BarSeries(symbol, bars);
    }
}
